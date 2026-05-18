// skill: redis-writer
// Atomic INCRBY with per-day expiry on user token counter keys.
package skills

import (
	"context"
	"fmt"
	"time"

	"github.com/redis/go-redis/v9"
)

type RedisWriter struct {
	rdb *redis.Client
}

func NewRedisWriter(rdb *redis.Client) *RedisWriter {
	return &RedisWriter{rdb: rdb}
}

// Increment atomically adds n tokens to user:<uid>:tokens:<YYYY-MM-DD>.
// Key expires after 48h so yesterday's data lingers for the rebalancer reconciliation.
func (rw *RedisWriter) Increment(userID string, n int) {
	ctx, cancel := context.WithTimeout(context.Background(), 200*time.Millisecond)
	defer cancel()

	today := time.Now().UTC().Format("2006-01-02")
	key := fmt.Sprintf("user:%s:tokens:%s", userID, today)

	pipe := rw.rdb.Pipeline()
	pipe.IncrBy(ctx, key, int64(n))
	pipe.Expire(ctx, key, 48*time.Hour)
	pipe.Exec(ctx) //nolint:errcheck — best-effort; missed increments reconcile via Admin API
}
