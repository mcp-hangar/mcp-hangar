package controller

import (
	"context"
	"os"
	"path/filepath"
	"testing"

	mcpv1alpha1 "github.com/mcp-hangar/mcp-hangar/operator/api/v1alpha1"
	"k8s.io/client-go/kubernetes/scheme"
	"k8s.io/client-go/rest"
	ctrl "sigs.k8s.io/controller-runtime"
	"sigs.k8s.io/controller-runtime/pkg/client"
	"sigs.k8s.io/controller-runtime/pkg/envtest"
	logf "sigs.k8s.io/controller-runtime/pkg/log"
	"sigs.k8s.io/controller-runtime/pkg/log/zap"
	metricsserver "sigs.k8s.io/controller-runtime/pkg/metrics/server"
)

var (
	testEnv   *envtest.Environment
	k8sClient client.Client
	cfg       *rest.Config
	ctx       context.Context
	cancel    context.CancelFunc
)

func TestMain(m *testing.M) {
	logf.SetLogger(zap.New(zap.WriteTo(os.Stderr), zap.UseDevMode(true)))

	ctx, cancel = context.WithCancel(context.Background())

	// Configure envtest with all 3 CRDs
	testEnv = &envtest.Environment{
		CRDDirectoryPaths:     []string{filepath.Join("..", "..", "..", "helm-charts", "mcp-hangar-operator", "crds")},
		ErrorIfCRDPathMissing: true,
	}

	var err error
	cfg, err = testEnv.Start()
	if err != nil {
		panic("failed to start envtest: " + err.Error())
	}
	if cfg == nil {
		panic("envtest config is nil")
	}

	// Register CRD scheme
	if err := mcpv1alpha1.AddToScheme(scheme.Scheme); err != nil {
		panic("failed to add scheme: " + err.Error())
	}

	// Create k8s client
	k8sClient, err = client.New(cfg, client.Options{Scheme: scheme.Scheme})
	if err != nil {
		panic("failed to create k8s client: " + err.Error())
	}

	// Create manager with metrics disabled to avoid port conflicts
	mgr, err := ctrl.NewManager(cfg, ctrl.Options{
		Scheme:  scheme.Scheme,
		Metrics: metricsserver.Options{BindAddress: "0"},
	})
	if err != nil {
		panic("failed to create manager: " + err.Error())
	}

	// Register MCPProviderGroup controller
	if err := (&MCPProviderGroupReconciler{
		Client:   mgr.GetClient(),
		Scheme:   mgr.GetScheme(),
		Recorder: mgr.GetEventRecorderFor("mcpprovidergroup-controller"),
	}).SetupWithManager(mgr); err != nil {
		panic("failed to setup MCPProviderGroup controller: " + err.Error())
	}

	// Register MCPDiscoverySource controller
	if err := (&MCPDiscoverySourceReconciler{
		Client:   mgr.GetClient(),
		Scheme:   mgr.GetScheme(),
		Recorder: mgr.GetEventRecorderFor("mcpdiscoverysource-controller"),
	}).SetupWithManager(mgr); err != nil {
		panic("failed to setup MCPDiscoverySource controller: " + err.Error())
	}

	// Start manager in background
	go func() {
		if err := mgr.Start(ctx); err != nil {
			panic("failed to start manager: " + err.Error())
		}
	}()

	// Run tests
	code := m.Run()

	// Cleanup
	cancel()
	if err := testEnv.Stop(); err != nil {
		panic("failed to stop envtest: " + err.Error())
	}
	os.Exit(code)
}
