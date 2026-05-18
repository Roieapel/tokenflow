package main

import (
	"log"
	"net/http"
	"os"

	"github.com/redis/go-redis/v9"
	"github.com/tokenflow/pool-agent/skills"
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
	poolKey := os.Getenv("ANTHROPIC_POOL_KEY") // real Anthropic key used for pool borrows

	// Single shared Redis client — one connection pool for the whole agent
	opts, err := redis.ParseURL(redisURL)
	if err != nil {
		log.Fatalf("invalid REDIS_URL %q: %v", redisURL, err)
	}
	rdb := redis.NewClient(opts)

	calc := skills.NewPoolCalculator(rdb)
	ledger := skills.NewBorrowLedger(rdb)
	executor := skills.NewRedirectExecutor(anthropicURL, poolKey, rdb)

	http.HandleFunc("/route", func(w http.ResponseWriter, r *http.Request) {
		userID := r.Header.Get("X-User-ID")
		role := r.Header.Get("X-User-Role")
		// Conservative per-request estimate. Actual usage is reconciled post-stream
		// by the counter-agent (engineers) or usage-sync-agent (non-engineers).
		const estimatedTokens int64 = 4096

		decision := calc.Evaluate(userID, role, estimatedTokens)

		switch decision {
		case skills.DecisionOwn:
			w.Header().Set("X-Route", "own")
			w.WriteHeader(http.StatusOK)

		case skills.DecisionBorrow:
			ledger.Record(userID, estimatedTokens)
			executor.Redirect(w, r, userID, estimatedTokens)

		case skills.DecisionBlock:
			http.Error(w, `{"error":"rate_limit","message":"Pool exhausted. Try again later."}`, http.StatusTooManyRequests)
		}
	})

	log.Println("pool-agent listening on :8083")
	log.Fatal(http.ListenAndServe(":8083", nil))
}
