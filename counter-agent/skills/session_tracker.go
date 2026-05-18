// skill: session-tracker
// Groups token counts by X-Session-ID for agentic Claude Code runs.
package skills

import (
	"context"
	"fmt"
	"time"

	"github.com/redis/go-redis/v9"
)

type SessionTracker struct {
	rdb *redis.Client
}

func NewSessionTracker(rdb *redis.Client) *SessionTracker {
	return &SessionTracker{rdb: rdb}
}

// Record appends n tokens to the session's running total.
// Key: session:<session_id>:user:<uid>:tokens  — expires after 7 days.
func (st *SessionTracker) Record(sessionID, userID string, n int) {
	ctx, cancel := context.WithTimeout(context.Background(), 200*time.Millisecond)
	defer cancel()

	key := fmt.Sprintf("session:%s:user:%s:tokens", sessionID, userID)
	pipe := st.rdb.Pipeline()
	pipe.IncrBy(ctx, key, int64(n))
	pipe.Expire(ctx, key, 7*24*time.Hour)
	pipe.Exec(ctx) //nolint:errcheck
}

// SessionTotal returns the total tokens consumed in a given session.
func (st *SessionTracker) SessionTotal(sessionID, userID string) (int64, error) {
	ctx, cancel := context.WithTimeout(context.Background(), 200*time.Millisecond)
	defer cancel()

	key := fmt.Sprintf("session:%s:user:%s:tokens", sessionID, userID)
	return st.rdb.Get(ctx, key).Int64()
}
