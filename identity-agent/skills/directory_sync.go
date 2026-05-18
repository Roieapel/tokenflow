// skill: directory-sync
// Pulls the HR/SSO directory (Okta SCIM) nightly and upserts user→role mappings into Redis.
package skills

import (
	"context"
	"encoding/json"
	"fmt"
	"log"
	"net/http"
	"os"
	"time"

	"github.com/redis/go-redis/v9"
)

type scimUser struct {
	ID          string `json:"id"`
	DisplayName string `json:"displayName"`
	Emails      []struct {
		Value string `json:"value"`
	} `json:"emails"`
	// Custom SCIM attribute set by HR system: engineer | pm | designer | ops
	TFRole string `json:"urn:tokenflow:role"`
}

type scimListResponse struct {
	Resources []scimUser `json:"Resources"`
}

type DirectorySync struct {
	rdb        *redis.Client
	oktaURL    string
	oktaToken  string
	httpClient *http.Client
}

func NewDirectorySync(redisURL string) *DirectorySync {
	opts, _ := redis.ParseURL(redisURL)
	return &DirectorySync{
		rdb:        redis.NewClient(opts),
		oktaURL:    os.Getenv("OKTA_SCIM_URL"),  // e.g. https://company.okta.com/scim/v2
		oktaToken:  os.Getenv("OKTA_SCIM_TOKEN"), // SCIM bearer token
		httpClient: &http.Client{Timeout: 30 * time.Second},
	}
}

// Sync fetches all users from Okta SCIM and writes them to Redis.
// Intended to be called nightly by a cron trigger.
func (ds *DirectorySync) Sync(ctx context.Context) error {
	req, _ := http.NewRequestWithContext(ctx, http.MethodGet, ds.oktaURL+"/Users?count=1000", nil)
	req.Header.Set("Authorization", "Bearer "+ds.oktaToken)

	resp, err := ds.httpClient.Do(req)
	if err != nil {
		return fmt.Errorf("SCIM fetch failed: %w", err)
	}
	defer resp.Body.Close()

	var list scimListResponse
	if err := json.NewDecoder(resp.Body).Decode(&list); err != nil {
		return fmt.Errorf("SCIM decode failed: %w", err)
	}

	pipe := ds.rdb.Pipeline()
	for _, u := range list.Resources {
		role := u.TFRole
		if role == "" {
			role = "ops" // default for unmapped users
		}
		pipe.Set(ctx, "user:"+u.ID+":name", u.DisplayName, 48*time.Hour)
		pipe.Set(ctx, "user:"+u.ID+":role", role, 48*time.Hour)
		log.Printf("[directory-sync] upserted user=%s role=%s", u.ID, role)
	}
	_, err = pipe.Exec(ctx)
	return err
}
