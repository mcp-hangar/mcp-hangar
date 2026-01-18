package hangar

import (
	"context"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"testing"
	"time"

	"github.com/stretchr/testify/assert"
	"github.com/stretchr/testify/require"
)

func TestClient_GetProviderTools_Success(t *testing.T) {
	// Setup mock server
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		assert.Equal(t, "/api/v1/providers/default/test-provider/tools", r.URL.Path)
		assert.Equal(t, "GET", r.Method)
		assert.Equal(t, "test-api-key", r.Header.Get("X-API-Key"))

		response := map[string]interface{}{
			"tools": []string{"tool1", "tool2", "tool3"},
		}
		w.Header().Set("Content-Type", "application/json")
		json.NewEncoder(w).Encode(response)
	}))
	defer server.Close()

	client := NewClient(&Config{
		URL:    server.URL,
		APIKey: "test-api-key",
	})

	// Execute
	tools, err := client.GetProviderTools(context.Background(), "test-provider", "default")

	// Assert
	require.NoError(t, err)
	assert.Len(t, tools, 3)
	assert.Contains(t, tools, "tool1")
	assert.Contains(t, tools, "tool2")
	assert.Contains(t, tools, "tool3")
}

func TestClient_GetProviderTools_NotFound(t *testing.T) {
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.WriteHeader(http.StatusNotFound)
		json.NewEncoder(w).Encode(map[string]string{
			"error": "provider not found",
		})
	}))
	defer server.Close()

	client := NewClient(&Config{
		URL:    server.URL,
		APIKey: "test-api-key",
	})

	tools, err := client.GetProviderTools(context.Background(), "nonexistent", "default")

	assert.Error(t, err)
	assert.Nil(t, tools)
	assert.Contains(t, err.Error(), "provider not found")
}

func TestClient_GetProviderTools_Timeout(t *testing.T) {
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		time.Sleep(100 * time.Millisecond)
	}))
	defer server.Close()

	client := NewClient(&Config{
		URL:     server.URL,
		APIKey:  "test-api-key",
		Timeout: 10 * time.Millisecond,
	})

	ctx := context.Background()
	tools, err := client.GetProviderTools(ctx, "test-provider", "default")

	assert.Error(t, err)
	assert.Nil(t, tools)
}

func TestClient_HealthCheckRemote_Healthy(t *testing.T) {
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		assert.Equal(t, "/api/v1/health/remote", r.URL.Path)
		assert.Equal(t, "POST", r.Method)

		response := map[string]interface{}{
			"healthy": true,
			"tools":   []string{"remote-tool1", "remote-tool2"},
		}
		w.Header().Set("Content-Type", "application/json")
		json.NewEncoder(w).Encode(response)
	}))
	defer server.Close()

	client := NewClient(&Config{
		URL:    server.URL,
		APIKey: "test-api-key",
	})

	healthy, tools, err := client.HealthCheckRemote(context.Background(), "https://api.example.com")

	require.NoError(t, err)
	assert.True(t, healthy)
	assert.Len(t, tools, 2)
	assert.Contains(t, tools, "remote-tool1")
}

func TestClient_HealthCheckRemote_Unhealthy(t *testing.T) {
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		response := map[string]interface{}{
			"healthy": false,
			"tools":   []string{},
			"error":   "connection refused",
		}
		w.Header().Set("Content-Type", "application/json")
		json.NewEncoder(w).Encode(response)
	}))
	defer server.Close()

	client := NewClient(&Config{
		URL:    server.URL,
		APIKey: "test-api-key",
	})

	healthy, tools, err := client.HealthCheckRemote(context.Background(), "https://broken.example.com")

	assert.Error(t, err)
	assert.False(t, healthy)
	assert.Empty(t, tools)
	assert.Contains(t, err.Error(), "connection refused")
}

func TestClient_RegisterProvider_Success(t *testing.T) {
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		assert.Equal(t, "/api/v1/providers", r.URL.Path)
		assert.Equal(t, "POST", r.Method)
		assert.Equal(t, "application/json", r.Header.Get("Content-Type"))

		var body RegisterProviderRequest
		err := json.NewDecoder(r.Body).Decode(&body)
		require.NoError(t, err)

		assert.Equal(t, "test-provider", body.Name)
		assert.Equal(t, "default", body.Namespace)

		w.WriteHeader(http.StatusCreated)
		json.NewEncoder(w).Encode(map[string]string{
			"status": "registered",
		})
	}))
	defer server.Close()

	client := NewClient(&Config{
		URL:    server.URL,
		APIKey: "test-api-key",
	})

	req := &RegisterProviderRequest{
		Name:      "test-provider",
		Namespace: "default",
		Mode:      "container",
		Image:     "test:latest",
	}

	err := client.RegisterProvider(context.Background(), req)

	assert.NoError(t, err)
}

func TestClient_DeregisterProvider_Success(t *testing.T) {
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		assert.Equal(t, "/api/v1/providers/default/test-provider", r.URL.Path)
		assert.Equal(t, "DELETE", r.Method)
		assert.Equal(t, "test-api-key", r.Header.Get("X-API-Key"))

		w.WriteHeader(http.StatusOK)
		json.NewEncoder(w).Encode(map[string]string{
			"status": "deregistered",
		})
	}))
	defer server.Close()

	client := NewClient(&Config{
		URL:    server.URL,
		APIKey: "test-api-key",
	})

	err := client.DeregisterProvider(context.Background(), "test-provider", "default")

	assert.NoError(t, err)
}

func TestClient_DeregisterProvider_NotFound(t *testing.T) {
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.WriteHeader(http.StatusNotFound)
	}))
	defer server.Close()

	client := NewClient(&Config{
		URL:    server.URL,
		APIKey: "test-api-key",
	})

	// Should not error on 404 - provider already gone
	err := client.DeregisterProvider(context.Background(), "nonexistent", "default")

	assert.NoError(t, err)
}

func TestClient_ServerError(t *testing.T) {
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.WriteHeader(http.StatusInternalServerError)
		json.NewEncoder(w).Encode(map[string]string{
			"error": "internal server error",
		})
	}))
	defer server.Close()

	client := NewClient(&Config{
		URL:    server.URL,
		APIKey: "test-api-key",
	})

	tools, err := client.GetProviderTools(context.Background(), "test-provider", "default")

	assert.Error(t, err)
	assert.Nil(t, tools)
	assert.Contains(t, err.Error(), "500")
}

func TestClient_InvalidJSON(t *testing.T) {
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		w.Write([]byte("invalid json {"))
	}))
	defer server.Close()

	client := NewClient(&Config{
		URL:    server.URL,
		APIKey: "test-api-key",
	})

	tools, err := client.GetProviderTools(context.Background(), "test-provider", "default")

	assert.Error(t, err)
	assert.Nil(t, tools)
}

func TestClient_ContextCancellation(t *testing.T) {
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		time.Sleep(100 * time.Millisecond)
	}))
	defer server.Close()

	client := NewClient(&Config{
		URL:    server.URL,
		APIKey: "test-api-key",
	})

	ctx, cancel := context.WithCancel(context.Background())
	cancel() // Cancel immediately

	tools, err := client.GetProviderTools(ctx, "test-provider", "default")

	assert.Error(t, err)
	assert.Nil(t, tools)
	assert.Contains(t, err.Error(), "context canceled")
}

func TestNewClient(t *testing.T) {
	client := NewClient(&Config{
		URL:    "http://localhost:8080",
		APIKey: "my-api-key",
	})

	assert.NotNil(t, client)
	assert.Equal(t, "http://localhost:8080", client.baseURL)
	assert.Equal(t, "my-api-key", client.apiKey)
	assert.NotNil(t, client.httpClient)
	assert.Equal(t, 30*time.Second, client.httpClient.Timeout)
}
