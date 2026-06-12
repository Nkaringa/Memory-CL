// Package server implements a tiny greeting server.
package server

import (
	"fmt"
	"strings"
)

// DefaultName is used when no name is given.
const DefaultName = "world"

// Handler greets people over the wire.
type Handler struct {
	Logger
	name  string
	count int
}

// NewHandler builds a Handler with a normalized name.
func NewHandler(name string) *Handler {
	if name == "" {
		name = DefaultName
	}
	return &Handler{name: strings.TrimSpace(name)}
}

// Greet returns the greeting and bumps the counter.
func (h *Handler) Greet(prefix string) string {
	h.count++
	return fmt.Sprintf("%s, %s!", prefix, h.name)
}

// Count reports how many greetings were served.
func (h Handler) Count() int {
	return h.count
}
