// Package main is the entry point for the MCP-Hangar Kubernetes Operator
package main

import (
	"flag"
	"os"

	// Import all Kubernetes client auth plugins (e.g. Azure, GCP, OIDC, etc.)
	_ "k8s.io/client-go/plugin/pkg/client/auth"

	"k8s.io/apimachinery/pkg/runtime"
	utilruntime "k8s.io/apimachinery/pkg/util/runtime"
	clientgoscheme "k8s.io/client-go/kubernetes/scheme"
	ctrl "sigs.k8s.io/controller-runtime"
	"sigs.k8s.io/controller-runtime/pkg/healthz"
	"sigs.k8s.io/controller-runtime/pkg/log/zap"
	metricsserver "sigs.k8s.io/controller-runtime/pkg/metrics/server"

	mcpv1alpha1 "github.com/mapyr/mcp-hangar/operator/api/v1alpha1"
	"github.com/mapyr/mcp-hangar/operator/internal/controller"
	"github.com/mapyr/mcp-hangar/operator/pkg/hangar"
)

var (
	scheme   = runtime.NewScheme()
	setupLog = ctrl.Log.WithName("setup")
)

func init() {
	utilruntime.Must(clientgoscheme.AddToScheme(scheme))
	utilruntime.Must(mcpv1alpha1.AddToScheme(scheme))
}

func main() {
	var metricsAddr string
	var enableLeaderElection bool
	var probeAddr string
	var hangarURL string
	var hangarAPIKey string
	var logLevel string

	flag.StringVar(&metricsAddr, "metrics-bind-address", ":8080", "The address the metric endpoint binds to.")
	flag.StringVar(&probeAddr, "health-probe-bind-address", ":8081", "The address the probe endpoint binds to.")
	flag.BoolVar(&enableLeaderElection, "leader-elect", false,
		"Enable leader election for controller manager. "+
			"Enabling this will ensure there is only one active controller manager.")
	flag.StringVar(&hangarURL, "hangar-url", "", "URL of MCP-Hangar core service")
	flag.StringVar(&hangarAPIKey, "hangar-api-key", "", "API key for MCP-Hangar core")
	flag.StringVar(&logLevel, "log-level", "info", "Log level (debug, info, warn, error)")

	opts := zap.Options{
		Development: logLevel == "debug",
	}
	opts.BindFlags(flag.CommandLine)
	flag.Parse()

	ctrl.SetLogger(zap.New(zap.UseFlagOptions(&opts)))

	// Environment variable overrides
	if envURL := os.Getenv("HANGAR_URL"); envURL != "" && hangarURL == "" {
		hangarURL = envURL
	}
	if envKey := os.Getenv("HANGAR_API_KEY"); envKey != "" && hangarAPIKey == "" {
		hangarAPIKey = envKey
	}

	mgr, err := ctrl.NewManager(ctrl.GetConfigOrDie(), ctrl.Options{
		Scheme: scheme,
		Metrics: metricsserver.Options{
			BindAddress: metricsAddr,
		},
		HealthProbeBindAddress: probeAddr,
		LeaderElection:         enableLeaderElection,
		LeaderElectionID:       "mcp-hangar-operator.mcp-hangar.io",
	})
	if err != nil {
		setupLog.Error(err, "unable to start manager")
		os.Exit(1)
	}

	// Initialize Hangar client (optional)
	var hangarClient *hangar.Client
	if hangarURL != "" {
		hangarClient = hangar.NewClient(&hangar.Config{
			URL:    hangarURL,
			APIKey: hangarAPIKey,
		})
		setupLog.Info("Hangar client configured", "url", hangarURL)
	} else {
		setupLog.Info("Hangar client not configured - running without Hangar core integration")
	}

	// Setup MCPProvider controller
	if err = (&controller.MCPProviderReconciler{
		Client:       mgr.GetClient(),
		Scheme:       mgr.GetScheme(),
		Recorder:     mgr.GetEventRecorderFor("mcpprovider-controller"),
		HangarClient: hangarClient,
		Config:       controller.DefaultReconcilerConfig(),
	}).SetupWithManager(mgr); err != nil {
		setupLog.Error(err, "unable to create controller", "controller", "MCPProvider")
		os.Exit(1)
	}

	// Health checks
	if err := mgr.AddHealthzCheck("healthz", healthz.Ping); err != nil {
		setupLog.Error(err, "unable to set up health check")
		os.Exit(1)
	}
	if err := mgr.AddReadyzCheck("readyz", healthz.Ping); err != nil {
		setupLog.Error(err, "unable to set up ready check")
		os.Exit(1)
	}

	setupLog.Info("starting manager")
	if err := mgr.Start(ctrl.SetupSignalHandler()); err != nil {
		setupLog.Error(err, "problem running manager")
		os.Exit(1)
	}
}
