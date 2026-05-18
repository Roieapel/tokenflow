package main

import (
	"log"
	"net/http"
	"os"

	"github.com/redis/go-redis/v9"
	"github.com/tokenflow/identity-agent/skills"
)

func main() {
	redisURL := os.Getenv("REDIS_URL")
	if redisURL == "" {
		redisURL = "redis://localhost:6379"
	}

	// Single shared Redis client — one connection pool for the whole agent
	opts, err := redis.ParseURL(redisURL)
	if err != nil {
		log.Fatalf("invalid REDIS_URL %q: %v", redisURL, err)
	}
	rdb := redis.NewClient(opts)

	validator := skills.NewKeyValidator(rdb)
	resolver := skills.NewRoleResolver(rdb)

	http.HandleFunc("/identify", func(w http.ResponseWriter, r *http.Request) {
		authHeader := r.Header.Get("Authorization")
		key, err := validator.ExtractAndValidate(authHeader)
		if err != nil {
			http.Error(w, "invalid key", http.StatusUnauthorized)
			return
		}

		user, err := resolver.Resolve(key)
		if err != nil {
			http.Error(w, "user not found", http.StatusUnauthorized)
			return
		}

		w.Header().Set("X-User-ID", user.ID)
		w.Header().Set("X-User-Role", user.Role)
		w.WriteHeader(http.StatusOK)
	})

	log.Println("identity-agent listening on :8081")
	log.Fatal(http.ListenAndServe(":8081", nil))
}
