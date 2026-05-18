package main

import (
	"log"
	"net/http"
	"net/http/httputil"
	"net/url"
	"os"

	"github.com/redis/go-redis/v9"
	"github.com/tokenflow/counter-agent/skills"
)

func main() {
	redisURL := os.Getenv("REDIS_URL")
	if redisURL == "" {
		redisURL = "redis://localhost:6379"
	}
	anthropicURL := os.Getenv("ANTHROPIC_API_URL")
	if anthropicURL == "" {
		anthropicURL = "https://api.anthropic.com"
	}

	// Single shared Redis client — one connection pool for the whole agent
	opts, err := redis.ParseURL(redisURL)
	if err != nil {
		log.Fatalf("invalid REDIS_URL %q: %v", redisURL, err)
	}
	rdb := redis.NewClient(opts)

	writer := skills.NewRedisWriter(rdb)
	tracker := skills.NewSessionTracker(rdb)
	parser := skills.NewStreamParser(writer, tracker)

	target, _ := url.Parse(anthropicURL)
	proxy := httputil.NewSingleHostReverseProxy(target)

	// Route by role:
	//   engineer        → intercept SSE stream and count tokens in real time
	//   pm/designer/ops → pass through; usage-sync-agent counts them via Admin API
	http.HandleFunc("/", func(w http.ResponseWriter, r *http.Request) {
		userID := r.Header.Get("X-User-ID")
		role := r.Header.Get("X-User-Role")
		sessionID := r.Header.Get("X-Session-ID")
		if sessionID == "" {
			sessionID = "default"
		}

		if role == "engineer" {
			iw := parser.NewInterceptWriter(w, userID, sessionID)
			proxy.ServeHTTP(iw, r)
			iw.Flush()
		} else {
			// Non-engineer: forward without SSE interception.
			// usage-sync-agent pulls authoritative counts from Admin API every 10-15 min.
			proxy.ServeHTTP(w, r)
		}
	})

	log.Println("counter-agent proxy listening on :8082")
	log.Fatal(http.ListenAndServe(":8082", nil))
}
