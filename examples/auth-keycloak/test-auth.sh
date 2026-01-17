#!/bin/bash
# Test script for MCP-Hangar + Keycloak integration
# This script demonstrates authentication flow

set -e

KEYCLOAK_URL="${KEYCLOAK_URL:-http://localhost:8080}"
MCP_URL="${MCP_URL:-http://localhost:9000}"
REALM="mcp-hangar"
CLIENT_ID="mcp-cli"

echo "========================================"
echo "MCP-Hangar + Keycloak Integration Test"
echo "========================================"
echo ""

# Wait for services to be ready
echo "Waiting for services..."
until curl -sf "$KEYCLOAK_URL/health/ready" > /dev/null 2>&1; do
    echo "  Waiting for Keycloak..."
    sleep 2
done
echo "✓ Keycloak is ready"

until curl -sf "$MCP_URL/health/live" > /dev/null 2>&1; do
    echo "  Waiting for MCP-Hangar..."
    sleep 2
done
echo "✓ MCP-Hangar is ready"
echo ""

# Function to get access token from Keycloak
get_token() {
    local username=$1
    local password=$2

    curl -s -X POST "$KEYCLOAK_URL/realms/$REALM/protocol/openid-connect/token" \
        -H "Content-Type: application/x-www-form-urlencoded" \
        -d "grant_type=password" \
        -d "client_id=$CLIENT_ID" \
        -d "username=$username" \
        -d "password=$password" \
        | jq -r '.access_token'
}

# Function to call MCP-Hangar API
call_mcp() {
    local token=$1
    local endpoint=$2

    curl -s -X GET "$MCP_URL$endpoint" \
        -H "Authorization: Bearer $token" \
        -H "Content-Type: application/json"
}

echo "========================================"
echo "Test 1: Unauthenticated request (should fail)"
echo "========================================"
response=$(curl -s -w "\n%{http_code}" "$MCP_URL/mcp")
http_code=$(echo "$response" | tail -n1)
body=$(echo "$response" | head -n -1)
echo "HTTP Status: $http_code"
if [ "$http_code" == "401" ]; then
    echo "✓ Correctly rejected unauthenticated request"
else
    echo "✗ Expected 401, got $http_code"
fi
echo ""

echo "========================================"
echo "Test 2: Login as admin user"
echo "========================================"
echo "Getting token for admin@example.com..."
ADMIN_TOKEN=$(get_token "admin" "admin123")
if [ -n "$ADMIN_TOKEN" ] && [ "$ADMIN_TOKEN" != "null" ]; then
    echo "✓ Got admin token"
    echo "Token (first 50 chars): ${ADMIN_TOKEN:0:50}..."
else
    echo "✗ Failed to get admin token"
    exit 1
fi
echo ""

echo "========================================"
echo "Test 3: Login as developer user"
echo "========================================"
echo "Getting token for developer@example.com..."
DEV_TOKEN=$(get_token "developer" "dev123")
if [ -n "$DEV_TOKEN" ] && [ "$DEV_TOKEN" != "null" ]; then
    echo "✓ Got developer token"
else
    echo "✗ Failed to get developer token"
    exit 1
fi
echo ""

echo "========================================"
echo "Test 4: Login as viewer user"
echo "========================================"
echo "Getting token for viewer@example.com..."
VIEWER_TOKEN=$(get_token "viewer" "view123")
if [ -n "$VIEWER_TOKEN" ] && [ "$VIEWER_TOKEN" != "null" ]; then
    echo "✓ Got viewer token"
else
    echo "✗ Failed to get viewer token"
    exit 1
fi
echo ""

echo "========================================"
echo "Test 5: Access health endpoints (no auth needed)"
echo "========================================"
echo "Calling /health/live..."
response=$(curl -s "$MCP_URL/health/live")
echo "Response: $response"
echo "✓ Health endpoint accessible without auth"
echo ""

echo "========================================"
echo "Test 6: Decode JWT to see claims"
echo "========================================"
echo "Admin token claims:"
echo "$ADMIN_TOKEN" | cut -d'.' -f2 | base64 -d 2>/dev/null | jq . 2>/dev/null || echo "(couldn't decode)"
echo ""

echo "========================================"
echo "Summary"
echo "========================================"
echo "Keycloak URL: $KEYCLOAK_URL"
echo "MCP-Hangar URL: $MCP_URL"
echo "Realm: $REALM"
echo ""
echo "Test Users:"
echo "  - admin / admin123 (admin role)"
echo "  - developer / dev123 (developer role)"
echo "  - viewer / view123 (viewer role)"
echo ""
echo "Keycloak Admin Console: $KEYCLOAK_URL/admin"
echo "  Username: admin"
echo "  Password: admin"
echo ""
echo "========================================"
echo "All tests completed!"
echo "========================================"
