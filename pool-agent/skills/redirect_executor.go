// skill: redirect-executor
// Rewrites the Authorization header to the pool key and forwards the request to Anthropic.
// Also publishes an event:redirect message to Redis so the dashboard live feed works.
package skills

import (
	"context"
	"encoding/json"
	"fmt"
	"net/http"
	"net/http/httputil"
	"net/url"
	"time"

	"github.com/redis/go-redis/v9"
)

type RedirectExecutor struct {
	proxy   *httputil.ReverseProxy
	poolKey string
	rdb     *redis.Client
}

func NewRedirectExecutor(anthropicURL, poolKey string, rdb *redis.Client) *RedirectExecutor {
	target, _ := url.Parse(anthropicURL)
	proxy := httputil.NewSingleHostReverseProxy(target)

	// Strip the vtk_ key; inject the real pool key before upstream sees it
	original := proxy.Director
	proxy.Director = func(r *http.Request) {
		original(r)
		r.Header.Set("Authorization", "Bearer "+poolKey)
		r.Header.Set("X-Routed-Via", "pool")
	}

	return &RedirectExecutor{proxy: proxy, poolKey: poolKey, rdb: rdb}
}

// Redirect forwards the request under the pool key and publishes a redirect event
// to Redis so the dashboard WebSocket feed shows live borrow activity.
func (re *RedirectExecutor) Redirect(w http.ResponseWriter, r *http.Request, borrowerID string, amount int64) {
	re.proxy.ServeHTTP(w, r)
	re.publishEvent(borrowerID, amount)
}

func (re *RedirectExecutor) publishEvent(borrowerID string, amount int64) {
	ctx, cancel := context.WithTimeout(context.Background(), 200*time.Millisecond)
	defer cancel()

	payload := map[string]interface{}{
		"ts":       time.Now().UTC().Format(time.RFC3339),
		"borrower": borrowerID,
		"amount":   amount,
	}
	b, err := json.Marshal(payload)
	if err != nil {
		return
	}
	re.rdb.Publish(ctx, "event:redirect", fmt.Sprintf("%s", b)) //nolint:errcheck
}
