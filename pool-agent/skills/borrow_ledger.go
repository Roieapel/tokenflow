// skill: borrow-ledger
// Append-only log of borrow events written to Redis Stream for audit/reconciliation.
//
// Bug fixes vs original:
//   - Added missing "fmt" import (TodayTotal wouldn't compile)
//   - TodayTotal is now O(1) via a dedicated per-user daily counter key rather
//     than scanning the full stream (which grows unbounded)
package skills

import (
	"context"
	"fmt"
	"time"

	"github.com/redis/go-redis/v9"
)

const ledgerStream = "ledger:borrows"

type BorrowLedger struct {
	rdb *redis.Client
}

func NewBorrowLedger(rdb *redis.Client) *BorrowLedger {
	return &BorrowLedger{rdb: rdb}
}

// Record appends a borrow event to the Redis Stream and increments a fast
// per-user daily counter used by TodayTotal.
func (bl *BorrowLedger) Record(borrowerID string, amount int64) {
	ctx, cancel := context.WithTimeout(context.Background(), 200*time.Millisecond)
	defer cancel()

	today := time.Now().UTC().Format("2006-01-02")
	counterKey := fmt.Sprintf("borrow:%s:%s", borrowerID, today)

	pipe := bl.rdb.Pipeline()
	// Audit trail — append-only stream
	pipe.XAdd(ctx, &redis.XAddArgs{
		Stream: ledgerStream,
		Values: map[string]interface{}{
			"borrower": borrowerID,
			"amount":   amount,
			"ts":       time.Now().UTC().Format(time.RFC3339),
		},
	})
	// Fast daily counter for TodayTotal (O(1) read)
	pipe.IncrBy(ctx, counterKey, amount)
	pipe.Expire(ctx, counterKey, 48*time.Hour)
	pipe.Exec(ctx) //nolint:errcheck
}

// TodayTotal returns total tokens borrowed by a user today — O(1) via counter key.
func (bl *BorrowLedger) TodayTotal(borrowerID string) int64 {
	ctx, cancel := context.WithTimeout(context.Background(), 100*time.Millisecond)
	defer cancel()

	today := time.Now().UTC().Format("2006-01-02")
	counterKey := fmt.Sprintf("borrow:%s:%s", borrowerID, today)

	n, err := bl.rdb.Get(ctx, counterKey).Int64()
	if err != nil {
		return 0
	}
	return n
}
