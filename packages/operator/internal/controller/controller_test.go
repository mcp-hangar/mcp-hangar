package controller

import (
	"testing"

	"github.com/stretchr/testify/assert"
)

func TestDefaultReconcilerConfig(t *testing.T) {
	config := DefaultReconcilerConfig()

	assert.NotNil(t, config)
	assert.Equal(t, 10, config.MaxConcurrentReconciles)
	assert.NotEmpty(t, config.DefaultImage)
}

func TestReconcilerConfig_Values(t *testing.T) {
	config := DefaultReconcilerConfig()

	// Verify default values are reasonable
	assert.Greater(t, config.ReadyRequeueInterval.Seconds(), float64(0))
	assert.Greater(t, config.ErrorRequeueInterval.Seconds(), float64(0))
	assert.Less(t, config.ErrorRequeueInterval, config.ReadyRequeueInterval)
}
