// skill: stream-parser
// Parses Anthropic SSE chunks and extracts usage.input_tokens / usage.output_tokens.
//
// Bug fix vs original: the old implementation called buf.Reset() after each Write,
// which silently dropped any partial line sitting at the end of a chunk. The new
// implementation keeps a []byte pending slice and only processes bytes up to the
// last complete newline, preserving the remainder for the next Write call.
package skills

import (
	"bytes"
	"encoding/json"
	"net/http"
	"strings"
)

type usagePayload struct {
	Usage struct {
		InputTokens  int `json:"input_tokens"`
		OutputTokens int `json:"output_tokens"`
	} `json:"usage"`
}

// InterceptWriter wraps an http.ResponseWriter, tees the SSE stream to count tokens.
type InterceptWriter struct {
	http.ResponseWriter
	userID    string
	sessionID string
	writer    *RedisWriter
	tracker   *SessionTracker
	pending   []byte // bytes received but not yet terminated by \n
	flushN    int
}

type StreamParser struct {
	writer  *RedisWriter
	tracker *SessionTracker
}

func NewStreamParser(w *RedisWriter, t *SessionTracker) *StreamParser {
	return &StreamParser{writer: w, tracker: t}
}

func (sp *StreamParser) NewInterceptWriter(w http.ResponseWriter, userID, sessionID string) *InterceptWriter {
	return &InterceptWriter{
		ResponseWriter: w,
		userID:         userID,
		sessionID:      sessionID,
		writer:         sp.writer,
		tracker:        sp.tracker,
	}
}

// Write intercepts each chunk from the SSE stream, counts tokens from complete
// lines, and forwards the raw bytes to the underlying ResponseWriter unchanged.
func (iw *InterceptWriter) Write(p []byte) (int, error) {
	// Accumulate with any leftover from the previous chunk
	data := append(iw.pending, p...)

	for {
		idx := bytes.IndexByte(data, '\n')
		if idx < 0 {
			break // no complete line yet — keep remainder in pending
		}
		line := strings.TrimRight(string(data[:idx]), "\r")
		data = data[idx+1:]
		iw.processLine(line)
	}

	// data now holds a partial line (or nothing) — save for next Write
	iw.pending = data

	return iw.ResponseWriter.Write(p)
}

// processLine extracts usage counts from a single SSE data line.
func (iw *InterceptWriter) processLine(line string) {
	if !strings.HasPrefix(line, "data: ") {
		return
	}
	payload := strings.TrimPrefix(line, "data: ")
	if payload == "[DONE]" {
		return
	}
	var u usagePayload
	if err := json.Unmarshal([]byte(payload), &u); err != nil {
		return
	}
	n := u.Usage.InputTokens + u.Usage.OutputTokens
	if n == 0 {
		return
	}
	iw.flushN += n
	if iw.flushN >= 10 {
		iw.flush()
	}
}

func (iw *InterceptWriter) flush() {
	iw.writer.Increment(iw.userID, iw.flushN)
	iw.tracker.Record(iw.sessionID, iw.userID, iw.flushN)
	iw.flushN = 0
}

// Flush finalises any remaining token count at stream end.
func (iw *InterceptWriter) Flush() {
	if iw.flushN > 0 {
		iw.flush()
	}
	iw.pending = nil
}
