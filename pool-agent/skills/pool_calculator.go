// skill: pool-calculator
// Computes available pool and decides request routing.
//
// Bug fix: the original check-then-decrement was two separate Redis calls,
// creating a TOCTOU race under concurrent load. The fix uses a Lua script that
// atomically checks the pool balance and decrements it in a single round-trip.
package skills

import (
	"context"
	"fmt"
	"time"

	"github.com/redis/go-redis/v9"
)

type Decision int

const (
	DecisionOwn    Decision = iota // user has personal quota remaining
	DecisionBorrow                 // personal quota exhausted, pool has capacity
	DecisionBlock                  // pool exhausted
)

// luaDecrIfSufficient atomically checks pool:available and decrements it only
// if the current balance is >= the requested amount. Returns 1 on success, 0 on
// insufficient balance. This eliminates the TOCTOU race of check-then-decrement.
const luaDecrIfSufficient = `
local pool = tonumber(redis.call('GET', KEYS[1]) or '0')
local amount = tonumber(ARGV[1])
if pool >= amount then
    redis.call('DECRBY', KEYS[1], amount)
    return 1
end
return 0
`

type PoolCalculator struct {
	rdb    *redis.Client
	script *redis.Script
}

func NewPoolCalculator(rdb *redis.Client) *PoolCalculator {
	return &PoolCalculator{
		rdb:    rdb,
		script: redis.NewScript(luaDecrIfSufficient),
	}
}

// Evaluate decides how to route a request for a given user.
func (pc *PoolCalculator) Evaluate(userID, role string, estimate int64) Decision {
	ctx, cancel := context.WithTimeout(context.Background(), 50*time.Millisecond)
	defer cancel()

	today := time.Now().UTC().Format("2006-01-02")
	usedKey := fmt.Sprintf("user:%s:tokens:%s", userID, today)
	allocKey := fmt.Sprintf("role:%s:daily_tokens", role)

	pipe := pc.rdb.Pipeline()
	usedCmd := pipe.Get(ctx, usedKey)
	allocCmd := pipe.Get(ctx, allocKey)
	pipe.Exec(ctx) //nolint:errcheck

	used, _ := usedCmd.Int64()
	alloc, _ := allocCmd.Int64()

	if used+estimate <= alloc {
		return DecisionOwn
	}

	// Atomic check-and-decrement via Lua — no TOCTOU race
	ok, err := pc.script.Run(ctx, pc.rdb, []string{"pool:available"}, estimate).Int()
	if err != nil || ok == 0 {
		return DecisionBlock
	}
	return DecisionBorrow
}
