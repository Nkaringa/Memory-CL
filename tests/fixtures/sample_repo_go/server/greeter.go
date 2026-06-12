// Package server implements a tiny greeting server.
package server

import "fmt"

// Greeter is anything that can greet.
type Greeter interface {
	Greet(prefix string) string
}

// Logger is embedded by Handler.
type Logger struct {
	prefix string
}

// Log writes one line with the configured prefix.
func (l *Logger) Log(msg string) {
	fmt.Println(l.prefix + msg)
}

// Greeting states a Handler can be in.
const (
	// StateIdle means no greeting in flight.
	StateIdle = iota
	StateBusy
)

// GreetAll greets every name using a fresh Handler.
func GreetAll(names []string) []string {
	out := make([]string, 0, len(names))
	for _, n := range names {
		h := NewHandler(n)
		out = append(out, h.Greet("Hello"))
	}
	return out
}
