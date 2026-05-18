// skill: key-validator
// Validates vtk_ prefix and checks Redis for key existence.
package skills

import (
	"context"
	"errors"
	"strings"
	"time"

	"github.com/redis/go-redis/v9"
)

type KeyValidator struct {
	rdb *redis.Client
}

func NewKeyValidator(rdb *redis.Client) *KeyValidator {
	return &KeyValidator{rdb: rdb}
}

// ExtractAndValidate pulls the vtk_ key from "Bearer vtk_..." and verifies it exists in Redis.
// Latency budget: < 1ms on hot path (Redis GET).
func (kv *KeyValidator) ExtractAndValidate(authHeader string) (string, error) {
	if !strings.HasPrefix(authHeader, "Bearer vtk_") {
		return "", errors.New("missing or malformed vtk_ bearer token")
	}
	key := strings.TrimPrefix(authHeader, "Bearer ")

	ctx, cancel := context.WithTimeout(context.Background(), 500*time.Millisecond)
	defer cancel()

	exists, err := kv.rdb.Exists(ctx, "key:"+key).Result()
	if err != nil {
		return "", err
	}
	if exists == 0 {
		return "", errors.New("key not found")
	}
	return key, nil
}
