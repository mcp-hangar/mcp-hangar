// Package hangar provides a client for communicating with MCP-Hangar core
package hangar

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"time"
)

// Client communicates with MCP-Hangar core
type Client struct {
	baseURL    string
	httpClient *http.Client
	apiKey     string
}

// Config holds client configuration
type Config struct {
	// URL of MCP-Hangar core service
	URL string

	// APIKey for authentication
	APIKey string

	// Timeout for requests
	Timeout time.Duration
}

// DefaultConfig returns default client configuration
func DefaultConfig() *Config {
	return &Config{
		URL:     "http://mcp-hangar.mcp-system.svc.cluster.local:8080",
		Timeout: 30 * time.Second,
	}
}

// NewClient creates a new Hangar client
func NewClient(config *Config) *Client {
	if config == nil {
		config = DefaultConfig()
	}

	timeout := config.Timeout
	if timeout == 0 {
		timeout = 30 * time.Second
	}

	return &Client{
		baseURL: config.URL,
		apiKey:  config.APIKey,
		httpClient: &http.Client{
			Timeout: timeout,
		},
	}
}

// ProviderInfo represents provider information from Hangar
type ProviderInfo struct {
	Name       string   `json:"name"`
	Namespace  string   `json:"namespace"`
	State      string   `json:"state"`
	Tools      []string `json:"tools"`
	ToolsCount int      `json:"tools_count"`
	Endpoint   string   `json:"endpoint,omitempty"`
}

// ToolInfo represents tool information
type ToolInfo struct {
	Name        string                 `json:"name"`
	Description string                 `json:"description,omitempty"`
	InputSchema map[string]interface{} `json:"input_schema,omitempty"`
}

// GetProviderTools fetches the list of tools from a provider
func (c *Client) GetProviderTools(ctx context.Context, name, namespace string) ([]string, error) {
	url := fmt.Sprintf("%s/api/v1/providers/%s/%s/tools", c.baseURL, namespace, name)

	req, err := http.NewRequestWithContext(ctx, http.MethodGet, url, nil)
	if err != nil {
		return nil, fmt.Errorf("failed to create request: %w", err)
	}

	c.setHeaders(req)

	resp, err := c.httpClient.Do(req)
	if err != nil {
		return nil, fmt.Errorf("request failed: %w", err)
	}
	defer resp.Body.Close()

	if resp.StatusCode == http.StatusNotFound {
		return nil, fmt.Errorf("provider not found: %s/%s", namespace, name)
	}

	if resp.StatusCode != http.StatusOK {
		body, _ := io.ReadAll(resp.Body)
		return nil, fmt.Errorf("unexpected status %d: %s", resp.StatusCode, string(body))
	}

	var result struct {
		Tools []string `json:"tools"`
	}
	if err := json.NewDecoder(resp.Body).Decode(&result); err != nil {
		return nil, fmt.Errorf("failed to decode response: %w", err)
	}

	return result.Tools, nil
}

// GetProvider fetches provider information
func (c *Client) GetProvider(ctx context.Context, name, namespace string) (*ProviderInfo, error) {
	url := fmt.Sprintf("%s/api/v1/providers/%s/%s", c.baseURL, namespace, name)

	req, err := http.NewRequestWithContext(ctx, http.MethodGet, url, nil)
	if err != nil {
		return nil, fmt.Errorf("failed to create request: %w", err)
	}

	c.setHeaders(req)

	resp, err := c.httpClient.Do(req)
	if err != nil {
		return nil, fmt.Errorf("request failed: %w", err)
	}
	defer resp.Body.Close()

	if resp.StatusCode == http.StatusNotFound {
		return nil, nil // Provider doesn't exist
	}

	if resp.StatusCode != http.StatusOK {
		body, _ := io.ReadAll(resp.Body)
		return nil, fmt.Errorf("unexpected status %d: %s", resp.StatusCode, string(body))
	}

	var info ProviderInfo
	if err := json.NewDecoder(resp.Body).Decode(&info); err != nil {
		return nil, fmt.Errorf("failed to decode response: %w", err)
	}

	return &info, nil
}

// HealthCheckRemote checks if a remote endpoint is healthy
func (c *Client) HealthCheckRemote(ctx context.Context, endpoint string) (bool, []string, error) {
	url := fmt.Sprintf("%s/api/v1/health/remote", c.baseURL)

	payload := struct {
		Endpoint string `json:"endpoint"`
	}{
		Endpoint: endpoint,
	}

	body, err := json.Marshal(payload)
	if err != nil {
		return false, nil, fmt.Errorf("failed to marshal request: %w", err)
	}

	req, err := http.NewRequestWithContext(ctx, http.MethodPost, url, bytes.NewReader(body))
	if err != nil {
		return false, nil, fmt.Errorf("failed to create request: %w", err)
	}

	c.setHeaders(req)
	req.Header.Set("Content-Type", "application/json")

	resp, err := c.httpClient.Do(req)
	if err != nil {
		return false, nil, fmt.Errorf("request failed: %w", err)
	}
	defer resp.Body.Close()

	var result struct {
		Healthy bool     `json:"healthy"`
		Tools   []string `json:"tools"`
		Error   string   `json:"error,omitempty"`
	}
	if err := json.NewDecoder(resp.Body).Decode(&result); err != nil {
		return false, nil, fmt.Errorf("failed to decode response: %w", err)
	}

	if result.Error != "" {
		return false, nil, fmt.Errorf("health check error: %s", result.Error)
	}

	return result.Healthy, result.Tools, nil
}

// RegisterProvider registers a provider with Hangar core
type RegisterProviderRequest struct {
	Name      string            `json:"name"`
	Namespace string            `json:"namespace"`
	Mode      string            `json:"mode"`
	Endpoint  string            `json:"endpoint,omitempty"`
	Image     string            `json:"image,omitempty"`
	Labels    map[string]string `json:"labels,omitempty"`
}

func (c *Client) RegisterProvider(ctx context.Context, req *RegisterProviderRequest) error {
	url := fmt.Sprintf("%s/api/v1/providers", c.baseURL)

	body, err := json.Marshal(req)
	if err != nil {
		return fmt.Errorf("failed to marshal request: %w", err)
	}

	httpReq, err := http.NewRequestWithContext(ctx, http.MethodPost, url, bytes.NewReader(body))
	if err != nil {
		return fmt.Errorf("failed to create request: %w", err)
	}

	c.setHeaders(httpReq)
	httpReq.Header.Set("Content-Type", "application/json")

	resp, err := c.httpClient.Do(httpReq)
	if err != nil {
		return fmt.Errorf("request failed: %w", err)
	}
	defer resp.Body.Close()

	if resp.StatusCode != http.StatusOK && resp.StatusCode != http.StatusCreated {
		respBody, _ := io.ReadAll(resp.Body)
		return fmt.Errorf("registration failed with status %d: %s", resp.StatusCode, string(respBody))
	}

	return nil
}

// DeregisterProvider removes a provider from Hangar core
func (c *Client) DeregisterProvider(ctx context.Context, name, namespace string) error {
	url := fmt.Sprintf("%s/api/v1/providers/%s/%s", c.baseURL, namespace, name)

	req, err := http.NewRequestWithContext(ctx, http.MethodDelete, url, nil)
	if err != nil {
		return fmt.Errorf("failed to create request: %w", err)
	}

	c.setHeaders(req)

	resp, err := c.httpClient.Do(req)
	if err != nil {
		return fmt.Errorf("request failed: %w", err)
	}
	defer resp.Body.Close()

	// 404 is OK - provider already gone
	if resp.StatusCode != http.StatusOK && resp.StatusCode != http.StatusNoContent && resp.StatusCode != http.StatusNotFound {
		body, _ := io.ReadAll(resp.Body)
		return fmt.Errorf("deregistration failed with status %d: %s", resp.StatusCode, string(body))
	}

	return nil
}

// StartProvider starts a cold provider
func (c *Client) StartProvider(ctx context.Context, name, namespace string) error {
	url := fmt.Sprintf("%s/api/v1/providers/%s/%s/start", c.baseURL, namespace, name)

	req, err := http.NewRequestWithContext(ctx, http.MethodPost, url, nil)
	if err != nil {
		return fmt.Errorf("failed to create request: %w", err)
	}

	c.setHeaders(req)

	resp, err := c.httpClient.Do(req)
	if err != nil {
		return fmt.Errorf("request failed: %w", err)
	}
	defer resp.Body.Close()

	if resp.StatusCode != http.StatusOK && resp.StatusCode != http.StatusAccepted {
		body, _ := io.ReadAll(resp.Body)
		return fmt.Errorf("start failed with status %d: %s", resp.StatusCode, string(body))
	}

	return nil
}

// StopProvider stops a provider
func (c *Client) StopProvider(ctx context.Context, name, namespace string) error {
	url := fmt.Sprintf("%s/api/v1/providers/%s/%s/stop", c.baseURL, namespace, name)

	req, err := http.NewRequestWithContext(ctx, http.MethodPost, url, nil)
	if err != nil {
		return fmt.Errorf("failed to create request: %w", err)
	}

	c.setHeaders(req)

	resp, err := c.httpClient.Do(req)
	if err != nil {
		return fmt.Errorf("request failed: %w", err)
	}
	defer resp.Body.Close()

	if resp.StatusCode != http.StatusOK && resp.StatusCode != http.StatusAccepted {
		body, _ := io.ReadAll(resp.Body)
		return fmt.Errorf("stop failed with status %d: %s", resp.StatusCode, string(body))
	}

	return nil
}

// Ping checks connectivity to Hangar core
func (c *Client) Ping(ctx context.Context) error {
	url := fmt.Sprintf("%s/health", c.baseURL)

	req, err := http.NewRequestWithContext(ctx, http.MethodGet, url, nil)
	if err != nil {
		return fmt.Errorf("failed to create request: %w", err)
	}

	resp, err := c.httpClient.Do(req)
	if err != nil {
		return fmt.Errorf("ping failed: %w", err)
	}
	defer resp.Body.Close()

	if resp.StatusCode != http.StatusOK {
		return fmt.Errorf("unhealthy: status %d", resp.StatusCode)
	}

	return nil
}

// setHeaders sets common headers for all requests
func (c *Client) setHeaders(req *http.Request) {
	if c.apiKey != "" {
		req.Header.Set("X-API-Key", c.apiKey)
	}
	req.Header.Set("Accept", "application/json")
	req.Header.Set("User-Agent", "mcp-hangar-operator/1.0")
}
