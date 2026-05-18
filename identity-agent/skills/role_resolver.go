// skill: role-resolver
// Maps a vtk_ key to a User (id + role) via Redis lookup.
package skills

import (
	"context"
	"errors"
	"time"

	"github.com/redis/go-redis/v9"
)

type User struct {
	ID   string
	Name string
	Role string // engineer | pm | designer | ops
}

type RoleResolver struct {
	rdb *redis.Client
}

func NewRoleResolver(rdb *redis.Client) *RoleResolver {
	return &RoleResolver{rdb: rdb}
}

// Resolve returns the User associated with a validated vtk_ key.
// Redis key layout (written by directory-sync):
//
//	key:<vtk_key>        → user_id  (string)
//	user:<uid>:name      → display name
//	user:<uid>:role      → role string
func (rr *RoleResolver) Resolve(vtk string) (*User, error) {
	ctx, cancel := context.WithTimeout(context.Background(), 500*time.Millisecond)
	defer cancel()

	uid, err := rr.rdb.Get(ctx, "key:"+vtk).Result()
	if err != nil {
		return nil, errors.New("key→user mapping not found")
	}

	pipe := rr.rdb.Pipeline()
	nameCmd := pipe.Get(ctx, "user:"+uid+":name")
	roleCmd := pipe.Get(ctx, "user:"+uid+":role")
	if _, err := pipe.Exec(ctx); err != nil {
		return nil, err
	}

	return &User{
		ID:   uid,
		Name: nameCmd.Val(),
		Role: roleCmd.Val(),
	}, nil
}
