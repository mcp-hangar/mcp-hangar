package controller

import (
	"context"
	"fmt"
	"regexp"
	"strings"
	"time"

	corev1 "k8s.io/api/core/v1"
	"k8s.io/apimachinery/pkg/api/errors"
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
	"k8s.io/apimachinery/pkg/runtime"
	"k8s.io/client-go/tools/record"
	ctrl "sigs.k8s.io/controller-runtime"
	"sigs.k8s.io/controller-runtime/pkg/client"
	"sigs.k8s.io/controller-runtime/pkg/controller/controllerutil"
	"sigs.k8s.io/controller-runtime/pkg/log"
	"sigs.k8s.io/yaml"

	mcpv1alpha1 "github.com/mcp-hangar/mcp-hangar/operator/api/v1alpha1"
	"github.com/mcp-hangar/mcp-hangar/operator/pkg/hangar"
	"github.com/mcp-hangar/mcp-hangar/operator/pkg/metrics"
)

const (
	// LabelDiscoveryManagedBy identifies MCPProviders created by a discovery source
	LabelDiscoveryManagedBy = "mcp-hangar.io/managed-by"

	// Condition types for discovery sources
	ConditionSynced = "Synced"
	ConditionPaused = "Paused"

	// Default refresh interval for rescanning
	defaultRefreshInterval = 1 * time.Minute

	// Paused requeue interval
	pausedRequeueAfter = 5 * time.Minute

	// Default excluded namespaces for namespace discovery
	defaultExcludeKubeSystem    = "kube-system"
	defaultExcludeKubePublic    = "kube-public"
	defaultExcludeKubeNodeLease = "kube-node-lease"

	// Default annotation prefix and required annotation
	defaultAnnotationPrefix   = "mcp-hangar.io"
	defaultRequiredAnnotation = "mcp-hangar.io/provider"
	annotationEndpointKey     = "mcp-hangar.io/endpoint"

	// Default service discovery settings
	defaultPortName = "mcp"
	defaultProtocol = "http"

	// Default ConfigMap key
	defaultConfigMapKey = "providers.yaml"

	// Event reasons for discovery
	ReasonSyncStarted   = "SyncStarted"
	ReasonSyncCompleted = "SyncCompleted"
	ReasonSyncFailed    = "SyncFailed"
	ReasonProviderFound = "ProviderFound"
	ReasonProviderGone  = "ProviderRemoved"
)

// DiscoveredProviderInfo holds information about a discovered provider
type DiscoveredProviderInfo struct {
	Name     string
	Source   string
	Endpoint string
	Mode     mcpv1alpha1.ProviderMode
	Labels   map[string]string
}

// ConfigMapProviderEntry defines a provider entry in a ConfigMap
type ConfigMapProviderEntry struct {
	Mode     string   `yaml:"mode"`
	Image    string   `yaml:"image,omitempty"`
	Endpoint string   `yaml:"endpoint,omitempty"`
	Command  []string `yaml:"command,omitempty"`
	Args     []string `yaml:"args,omitempty"`
}

// MCPDiscoverySourceReconciler reconciles a MCPDiscoverySource object
type MCPDiscoverySourceReconciler struct {
	client.Client
	Scheme       *runtime.Scheme
	Recorder     record.EventRecorder
	HangarClient *hangar.Client
}

// +kubebuilder:rbac:groups=mcp-hangar.io,resources=mcpdiscoverysources,verbs=get;list;watch;create;update;patch;delete
// +kubebuilder:rbac:groups=mcp-hangar.io,resources=mcpdiscoverysources/status,verbs=get;update;patch
// +kubebuilder:rbac:groups=mcp-hangar.io,resources=mcpdiscoverysources/finalizers,verbs=update
// +kubebuilder:rbac:groups=mcp-hangar.io,resources=mcpproviders,verbs=get;list;watch;create;update;patch;delete
// +kubebuilder:rbac:groups="",resources=namespaces,verbs=get;list;watch
// +kubebuilder:rbac:groups="",resources=configmaps,verbs=get;list;watch
// +kubebuilder:rbac:groups="",resources=services,verbs=get;list;watch
// +kubebuilder:rbac:groups="",resources=pods,verbs=get;list;watch
// +kubebuilder:rbac:groups="",resources=events,verbs=create;patch

// Reconcile performs the reconciliation loop for MCPDiscoverySource
func (r *MCPDiscoverySourceReconciler) Reconcile(ctx context.Context, req ctrl.Request) (ctrl.Result, error) {
	logger := log.FromContext(ctx)
	startTime := time.Now()

	logger.Info("Reconciling MCPDiscoverySource", "namespacedName", req.NamespacedName)
	defer func() {
		duration := time.Since(startTime)
		metrics.ReconcileDuration.WithLabelValues("mcpdiscoverysource").Observe(duration.Seconds())
	}()

	// Fetch the MCPDiscoverySource instance
	source := &mcpv1alpha1.MCPDiscoverySource{}
	if err := r.Get(ctx, req.NamespacedName, source); err != nil {
		if errors.IsNotFound(err) {
			logger.Info("MCPDiscoverySource resource not found, ignoring")
			return ctrl.Result{}, nil
		}
		logger.Error(err, "Failed to get MCPDiscoverySource")
		metrics.ReconcileTotal.WithLabelValues("mcpdiscoverysource", "error").Inc()
		return ctrl.Result{}, err
	}

	// Handle deletion
	if !source.ObjectMeta.DeletionTimestamp.IsZero() {
		result, err := r.reconcileDelete(ctx, source)
		if err != nil {
			metrics.ReconcileTotal.WithLabelValues("mcpdiscoverysource", "error").Inc()
		} else {
			metrics.ReconcileTotal.WithLabelValues("mcpdiscoverysource", "success").Inc()
		}
		return result, err
	}

	// Add finalizer if not present
	if !controllerutil.ContainsFinalizer(source, finalizerName) {
		controllerutil.AddFinalizer(source, finalizerName)
		if err := r.Update(ctx, source); err != nil {
			return ctrl.Result{}, err
		}
		return ctrl.Result{Requeue: true}, nil
	}

	// Main reconciliation logic
	result, err := r.reconcileNormal(ctx, source)
	if err != nil {
		metrics.ReconcileTotal.WithLabelValues("mcpdiscoverysource", "error").Inc()
	} else {
		metrics.ReconcileTotal.WithLabelValues("mcpdiscoverysource", "success").Inc()
	}

	return result, err
}

// reconcileNormal handles normal (non-deletion) reconciliation
func (r *MCPDiscoverySourceReconciler) reconcileNormal(ctx context.Context, source *mcpv1alpha1.MCPDiscoverySource) (ctrl.Result, error) {
	logger := log.FromContext(ctx)

	// Paused check FIRST -- if paused, freeze all operations
	if source.IsPaused() {
		logger.Info("MCPDiscoverySource is paused, skipping sync")
		source.Status.SetCondition(ConditionPaused, metav1.ConditionTrue, "Paused", "Discovery is paused")
		source.Status.SetCondition(ConditionSynced, metav1.ConditionUnknown, "Paused", "Sync suspended while paused")
		if err := r.Status().Update(ctx, source); err != nil {
			return ctrl.Result{}, err
		}
		return ctrl.Result{RequeueAfter: pausedRequeueAfter}, nil
	}

	// Clear paused condition
	source.Status.SetCondition(ConditionPaused, metav1.ConditionFalse, "Active", "Discovery is active")

	// Update ObservedGeneration
	source.Status.ObservedGeneration = source.Generation

	// Parse refresh interval
	refreshInterval := defaultRefreshInterval
	if source.Spec.RefreshInterval != "" {
		parsed, err := time.ParseDuration(source.Spec.RefreshInterval)
		if err != nil {
			logger.Error(err, "Failed to parse refreshInterval, using default", "refreshInterval", source.Spec.RefreshInterval)
		} else {
			refreshInterval = parsed
		}
	}

	// Start sync timer
	syncStart := time.Now()
	r.Recorder.Event(source, corev1.EventTypeNormal, ReasonSyncStarted, "Starting discovery sync")

	// Discover providers
	discovered, scanErrors, err := r.discoverProviders(ctx, source)
	if err != nil {
		logger.Error(err, "Discovery failed completely")
		source.Status.LastSyncError = err.Error()
		source.Status.SetCondition(ConditionSynced, metav1.ConditionFalse, "DiscoveryFailed", err.Error())
		source.Status.SetCondition(ConditionReady, metav1.ConditionFalse, "DiscoveryFailed", err.Error())
		if statusErr := r.Status().Update(ctx, source); statusErr != nil {
			return ctrl.Result{}, statusErr
		}
		r.Recorder.Event(source, corev1.EventTypeWarning, ReasonSyncFailed, fmt.Sprintf("Discovery failed: %v", err))
		return ctrl.Result{RequeueAfter: errorRequeueAfter}, nil
	}

	// Apply filters
	discovered = r.applyFilters(source, discovered)

	// Create or update providers
	var createErrors []string
	managedCount := int32(0)
	discoveredProviderStatuses := make([]mcpv1alpha1.DiscoveredProvider, 0, len(discovered))

	for name, info := range discovered {
		dp := mcpv1alpha1.DiscoveredProvider{
			Name:         name,
			Source:       info.Source,
			DiscoveredAt: metav1.Now(),
			Managed:      true,
		}

		if err := r.createOrUpdateProvider(ctx, source, info); err != nil {
			logger.Error(err, "Failed to create/update provider", "provider", name)
			createErrors = append(createErrors, fmt.Sprintf("%s: %v", name, err))
			dp.Managed = false
			dp.Error = err.Error()
		} else {
			managedCount++
		}

		discoveredProviderStatuses = append(discoveredProviderStatuses, dp)
	}

	// Authoritative sync: delete providers no longer discovered
	if source.IsAuthoritative() {
		if deleteErrs := r.authoritativeSync(ctx, source, discovered); len(deleteErrs) > 0 {
			createErrors = append(createErrors, deleteErrs...)
		}
	}

	// Update status
	syncDuration := time.Since(syncStart)
	now := metav1.Now()
	nextSync := metav1.NewTime(now.Add(refreshInterval))

	source.Status.DiscoveredCount = int32(len(discovered))
	source.Status.ManagedCount = managedCount
	source.Status.LastSyncTime = &now
	source.Status.LastSyncDuration = syncDuration.String()
	source.Status.NextSyncTime = &nextSync
	source.Status.DiscoveredProviders = discoveredProviderStatuses

	// Collect all errors
	allErrors := append(scanErrors, createErrors...)
	if len(allErrors) > 0 {
		source.Status.LastSyncError = strings.Join(allErrors, "; ")
		source.Status.SetCondition(ConditionSynced, metav1.ConditionFalse, "PartialFailure", source.Status.LastSyncError)
	} else {
		source.Status.LastSyncError = ""
		source.Status.SetCondition(ConditionSynced, metav1.ConditionTrue, "SyncCompleted", fmt.Sprintf("Discovered %d providers", len(discovered)))
	}

	// Ready condition: True if sync succeeded even partially (providers were discovered)
	if len(discovered) > 0 || len(allErrors) == 0 {
		source.Status.SetCondition(ConditionReady, metav1.ConditionTrue, "Ready", fmt.Sprintf("Managing %d providers", managedCount))
	} else {
		source.Status.SetCondition(ConditionReady, metav1.ConditionFalse, "NoProviders", "No providers discovered")
	}

	// Update metrics
	metrics.DiscoverySourceCount.WithLabelValues(source.Namespace, source.Name).Set(float64(len(discovered)))
	metrics.DiscoverySyncDuration.WithLabelValues(source.Namespace, source.Name).Observe(syncDuration.Seconds())

	if err := r.Status().Update(ctx, source); err != nil {
		return ctrl.Result{}, err
	}

	r.Recorder.Event(source, corev1.EventTypeNormal, ReasonSyncCompleted,
		fmt.Sprintf("Sync completed: discovered=%d, managed=%d, errors=%d", len(discovered), managedCount, len(allErrors)))

	return ctrl.Result{RequeueAfter: refreshInterval}, nil
}

// discoverProviders routes to the appropriate discovery method based on type
func (r *MCPDiscoverySourceReconciler) discoverProviders(ctx context.Context, source *mcpv1alpha1.MCPDiscoverySource) (map[string]DiscoveredProviderInfo, []string, error) {
	switch source.Spec.Type {
	case mcpv1alpha1.DiscoveryTypeNamespace:
		return r.discoverNamespace(ctx, source)
	case mcpv1alpha1.DiscoveryTypeConfigMap:
		return r.discoverConfigMap(ctx, source)
	case mcpv1alpha1.DiscoveryTypeAnnotations:
		return r.discoverAnnotations(ctx, source)
	case mcpv1alpha1.DiscoveryTypeServiceDiscovery:
		return r.discoverServices(ctx, source)
	default:
		return nil, nil, fmt.Errorf("unknown discovery type: %s", source.Spec.Type)
	}
}

// discoverNamespace discovers providers by scanning namespaces matching labels
func (r *MCPDiscoverySourceReconciler) discoverNamespace(ctx context.Context, source *mcpv1alpha1.MCPDiscoverySource) (map[string]DiscoveredProviderInfo, []string, error) {
	logger := log.FromContext(ctx)
	discovered := make(map[string]DiscoveredProviderInfo)
	var scanErrors []string

	if source.Spec.NamespaceSelector == nil {
		return discovered, nil, nil
	}

	// Build list options from MatchLabels
	listOpts := []client.ListOption{}
	if len(source.Spec.NamespaceSelector.MatchLabels) > 0 {
		listOpts = append(listOpts, client.MatchingLabels(source.Spec.NamespaceSelector.MatchLabels))
	}

	// List namespaces
	nsList := &corev1.NamespaceList{}
	if err := r.List(ctx, nsList, listOpts...); err != nil {
		return nil, nil, fmt.Errorf("failed to list namespaces: %w", err)
	}

	// Build exclude set
	excludeSet := map[string]bool{
		defaultExcludeKubeSystem:    true,
		defaultExcludeKubePublic:    true,
		defaultExcludeKubeNodeLease: true,
	}
	if source.Spec.NamespaceSelector.ExcludeNamespaces != nil {
		excludeSet = make(map[string]bool)
		for _, ns := range source.Spec.NamespaceSelector.ExcludeNamespaces {
			excludeSet[ns] = true
		}
	}

	// Check MatchExpressions if set
	for _, ns := range nsList.Items {
		if excludeSet[ns.Name] {
			continue
		}

		// Check MatchExpressions (if any)
		if len(source.Spec.NamespaceSelector.MatchExpressions) > 0 {
			if !matchesExpressions(ns.Labels, source.Spec.NamespaceSelector.MatchExpressions) {
				continue
			}
		}

		providerName := fmt.Sprintf("%s-%s", source.Name, ns.Name)
		discovered[providerName] = DiscoveredProviderInfo{
			Name:   providerName,
			Source: fmt.Sprintf("namespace/%s", ns.Name),
			Mode:   mcpv1alpha1.ProviderModeRemote,
			Labels: map[string]string{
				"discovery-namespace": ns.Name,
			},
		}
		logger.Info("Discovered provider from namespace", "namespace", ns.Name, "provider", providerName)
	}

	return discovered, scanErrors, nil
}

// discoverConfigMap discovers providers from a ConfigMap containing YAML definitions
func (r *MCPDiscoverySourceReconciler) discoverConfigMap(ctx context.Context, source *mcpv1alpha1.MCPDiscoverySource) (map[string]DiscoveredProviderInfo, []string, error) {
	logger := log.FromContext(ctx)
	discovered := make(map[string]DiscoveredProviderInfo)

	if source.Spec.ConfigMapRef == nil {
		return discovered, nil, nil
	}

	// Determine ConfigMap namespace
	cmNamespace := source.Spec.ConfigMapRef.Namespace
	if cmNamespace == "" {
		cmNamespace = source.Namespace
	}

	// Determine ConfigMap key
	cmKey := source.Spec.ConfigMapRef.Key
	if cmKey == "" {
		cmKey = defaultConfigMapKey
	}

	// Fetch ConfigMap
	cm := &corev1.ConfigMap{}
	cmObjKey := client.ObjectKey{Name: source.Spec.ConfigMapRef.Name, Namespace: cmNamespace}
	if err := r.Get(ctx, cmObjKey, cm); err != nil {
		return nil, nil, fmt.Errorf("failed to get ConfigMap %s/%s: %w", cmNamespace, source.Spec.ConfigMapRef.Name, err)
	}

	// Read provider definitions from the key
	data, ok := cm.Data[cmKey]
	if !ok {
		return nil, []string{fmt.Sprintf("key %q not found in ConfigMap %s/%s", cmKey, cmNamespace, source.Spec.ConfigMapRef.Name)}, nil
	}

	// Parse YAML
	var entries map[string]ConfigMapProviderEntry
	if err := yaml.Unmarshal([]byte(data), &entries); err != nil {
		return nil, nil, fmt.Errorf("failed to parse providers YAML from ConfigMap: %w", err)
	}

	for name, entry := range entries {
		providerName := fmt.Sprintf("%s-%s", source.Name, name)
		mode := mcpv1alpha1.ProviderModeRemote
		if entry.Mode == "container" {
			mode = mcpv1alpha1.ProviderModeContainer
		}

		discovered[providerName] = DiscoveredProviderInfo{
			Name:     providerName,
			Source:   fmt.Sprintf("configmap/%s", source.Spec.ConfigMapRef.Name),
			Endpoint: entry.Endpoint,
			Mode:     mode,
			Labels: map[string]string{
				"discovery-configmap": source.Spec.ConfigMapRef.Name,
				"discovery-entry":     name,
			},
		}
		logger.Info("Discovered provider from ConfigMap", "configmap", source.Spec.ConfigMapRef.Name, "entry", name, "provider", providerName)
	}

	return discovered, nil, nil
}

// discoverAnnotations discovers providers from annotated Pods and Services
func (r *MCPDiscoverySourceReconciler) discoverAnnotations(ctx context.Context, source *mcpv1alpha1.MCPDiscoverySource) (map[string]DiscoveredProviderInfo, []string, error) {
	logger := log.FromContext(ctx)
	discovered := make(map[string]DiscoveredProviderInfo)
	var scanErrors []string

	if source.Spec.Annotations == nil {
		return discovered, nil, nil
	}

	annotationPrefix := source.Spec.Annotations.AnnotationPrefix
	if annotationPrefix == "" {
		annotationPrefix = defaultAnnotationPrefix
	}

	requiredAnnotations := source.Spec.Annotations.RequiredAnnotations
	if len(requiredAnnotations) == 0 {
		requiredAnnotations = []string{defaultRequiredAnnotation}
	}

	providerAnnotation := requiredAnnotations[0]

	// Discover from Pods
	if len(source.Spec.Annotations.PodSelector) > 0 {
		podList := &corev1.PodList{}
		if err := r.List(ctx, podList,
			client.InNamespace(source.Namespace),
			client.MatchingLabels(source.Spec.Annotations.PodSelector),
		); err != nil {
			scanErrors = append(scanErrors, fmt.Sprintf("failed to list pods: %v", err))
		} else {
			for _, pod := range podList.Items {
				if !hasRequiredAnnotations(pod.Annotations, requiredAnnotations) {
					continue
				}

				providerName := pod.Annotations[providerAnnotation]
				if providerName == "" {
					providerName = pod.Name
				}

				endpoint := pod.Annotations[annotationEndpointKey]
				if endpoint == "" {
					// Construct from pod IP
					if pod.Status.PodIP != "" {
						endpoint = fmt.Sprintf("http://%s:8080", pod.Status.PodIP)
					}
				}

				fullName := fmt.Sprintf("%s-%s", source.Name, providerName)
				discovered[fullName] = DiscoveredProviderInfo{
					Name:     fullName,
					Source:   fmt.Sprintf("annotation/Pod/%s", pod.Name),
					Endpoint: endpoint,
					Mode:     mcpv1alpha1.ProviderModeRemote,
					Labels: map[string]string{
						"discovery-kind":     "Pod",
						"discovery-resource": pod.Name,
					},
				}
				logger.Info("Discovered provider from Pod annotation", "pod", pod.Name, "provider", fullName)
			}
		}
	}

	// Discover from Services
	if len(source.Spec.Annotations.ServiceSelector) > 0 {
		svcList := &corev1.ServiceList{}
		if err := r.List(ctx, svcList,
			client.InNamespace(source.Namespace),
			client.MatchingLabels(source.Spec.Annotations.ServiceSelector),
		); err != nil {
			scanErrors = append(scanErrors, fmt.Sprintf("failed to list services: %v", err))
		} else {
			for _, svc := range svcList.Items {
				if !hasRequiredAnnotations(svc.Annotations, requiredAnnotations) {
					continue
				}

				providerName := svc.Annotations[providerAnnotation]
				if providerName == "" {
					providerName = svc.Name
				}

				endpoint := svc.Annotations[annotationEndpointKey]
				if endpoint == "" {
					// Construct from service
					port := int32(8080)
					for _, p := range svc.Spec.Ports {
						if p.Name == defaultPortName || p.Name == "http" {
							port = p.Port
							break
						}
					}
					endpoint = fmt.Sprintf("http://%s.%s.svc.cluster.local:%d", svc.Name, svc.Namespace, port)
				}

				fullName := fmt.Sprintf("%s-%s", source.Name, providerName)
				discovered[fullName] = DiscoveredProviderInfo{
					Name:     fullName,
					Source:   fmt.Sprintf("annotation/Service/%s", svc.Name),
					Endpoint: endpoint,
					Mode:     mcpv1alpha1.ProviderModeRemote,
					Labels: map[string]string{
						"discovery-kind":     "Service",
						"discovery-resource": svc.Name,
					},
				}
				logger.Info("Discovered provider from Service annotation", "service", svc.Name, "provider", fullName)
			}
		}
	}

	return discovered, scanErrors, nil
}

// discoverServices discovers providers from Kubernetes Services matching a label selector
func (r *MCPDiscoverySourceReconciler) discoverServices(ctx context.Context, source *mcpv1alpha1.MCPDiscoverySource) (map[string]DiscoveredProviderInfo, []string, error) {
	logger := log.FromContext(ctx)
	discovered := make(map[string]DiscoveredProviderInfo)

	if source.Spec.ServiceDiscovery == nil {
		return discovered, nil, nil
	}

	portName := source.Spec.ServiceDiscovery.PortName
	if portName == "" {
		portName = defaultPortName
	}

	protocol := source.Spec.ServiceDiscovery.Protocol
	if protocol == "" {
		protocol = defaultProtocol
	}

	// List services matching selector
	svcList := &corev1.ServiceList{}
	listOpts := []client.ListOption{
		client.InNamespace(source.Namespace),
	}
	if len(source.Spec.ServiceDiscovery.Selector) > 0 {
		listOpts = append(listOpts, client.MatchingLabels(source.Spec.ServiceDiscovery.Selector))
	}

	if err := r.List(ctx, svcList, listOpts...); err != nil {
		return nil, nil, fmt.Errorf("failed to list services: %w", err)
	}

	for _, svc := range svcList.Items {
		// Find the named port
		var svcPort int32
		found := false
		for _, p := range svc.Spec.Ports {
			if p.Name == portName {
				svcPort = p.Port
				found = true
				break
			}
		}

		if !found {
			continue
		}

		endpoint := fmt.Sprintf("%s://%s.%s.svc.cluster.local:%d", protocol, svc.Name, svc.Namespace, svcPort)
		providerName := fmt.Sprintf("%s-%s", source.Name, svc.Name)

		discovered[providerName] = DiscoveredProviderInfo{
			Name:     providerName,
			Source:   fmt.Sprintf("service/%s", svc.Name),
			Endpoint: endpoint,
			Mode:     mcpv1alpha1.ProviderModeRemote,
			Labels: map[string]string{
				"discovery-service": svc.Name,
			},
		}
		logger.Info("Discovered provider from Service", "service", svc.Name, "endpoint", endpoint, "provider", providerName)
	}

	return discovered, nil, nil
}

// createOrUpdateProvider creates or updates an MCPProvider CR for a discovered provider
func (r *MCPDiscoverySourceReconciler) createOrUpdateProvider(ctx context.Context, source *mcpv1alpha1.MCPDiscoverySource, info DiscoveredProviderInfo) error {
	provider := &mcpv1alpha1.MCPProvider{
		ObjectMeta: metav1.ObjectMeta{
			Name:      info.Name,
			Namespace: source.Namespace,
		},
	}

	_, err := controllerutil.CreateOrUpdate(ctx, r.Client, provider, func() error {
		// Set managed-by label
		if provider.Labels == nil {
			provider.Labels = make(map[string]string)
		}
		provider.Labels[LabelDiscoveryManagedBy] = source.Name

		// Apply template labels
		if source.Spec.ProviderTemplate != nil && source.Spec.ProviderTemplate.Metadata != nil {
			for k, v := range source.Spec.ProviderTemplate.Metadata.Labels {
				provider.Labels[k] = v
			}
		}

		// Apply discovery-specific labels
		for k, v := range info.Labels {
			provider.Labels[k] = v
		}

		// Apply template annotations
		if source.Spec.ProviderTemplate != nil && source.Spec.ProviderTemplate.Metadata != nil {
			if provider.Annotations == nil {
				provider.Annotations = make(map[string]string)
			}
			for k, v := range source.Spec.ProviderTemplate.Metadata.Annotations {
				provider.Annotations[k] = v
			}
		}

		// Apply template spec if present
		if source.Spec.ProviderTemplate != nil && source.Spec.ProviderTemplate.Spec != nil {
			provider.Spec = *source.Spec.ProviderTemplate.Spec.DeepCopy()
		}

		// Override with discovered values
		provider.Spec.Mode = info.Mode
		if info.Endpoint != "" {
			provider.Spec.Endpoint = info.Endpoint
		}

		// Set controller owner reference if configured
		if source.ShouldSetController() {
			if err := controllerutil.SetControllerReference(source, provider, r.Scheme); err != nil {
				return fmt.Errorf("failed to set controller reference: %w", err)
			}
		}

		return nil
	})

	return err
}

// authoritativeSync deletes MCPProviders that are no longer discovered (scoped to successfully-scanned sources only)
func (r *MCPDiscoverySourceReconciler) authoritativeSync(ctx context.Context, source *mcpv1alpha1.MCPDiscoverySource, discovered map[string]DiscoveredProviderInfo) []string {
	logger := log.FromContext(ctx)
	var deleteErrors []string

	// List all MCPProviders managed by this source
	providerList := &mcpv1alpha1.MCPProviderList{}
	if err := r.List(ctx, providerList,
		client.InNamespace(source.Namespace),
		client.MatchingLabels{LabelDiscoveryManagedBy: source.Name},
	); err != nil {
		deleteErrors = append(deleteErrors, fmt.Sprintf("failed to list managed providers: %v", err))
		return deleteErrors
	}

	for i := range providerList.Items {
		existing := &providerList.Items[i]
		if _, found := discovered[existing.Name]; !found {
			// Provider no longer discovered -- delete it
			logger.Info("Authoritative sync: deleting provider no longer discovered", "provider", existing.Name)
			if err := r.Delete(ctx, existing); err != nil && !errors.IsNotFound(err) {
				logger.Error(err, "Failed to delete provider during authoritative sync", "provider", existing.Name)
				deleteErrors = append(deleteErrors, fmt.Sprintf("delete %s: %v", existing.Name, err))
			} else {
				r.Recorder.Event(source, corev1.EventTypeNormal, ReasonProviderGone,
					fmt.Sprintf("Deleted provider %s (no longer discovered)", existing.Name))
			}
		}
	}

	return deleteErrors
}

// applyFilters applies include/exclude patterns and max provider count to discovered providers
func (r *MCPDiscoverySourceReconciler) applyFilters(source *mcpv1alpha1.MCPDiscoverySource, discovered map[string]DiscoveredProviderInfo) map[string]DiscoveredProviderInfo {
	if source.Spec.Filters == nil {
		return discovered
	}

	logger := log.Log.WithName("discovery-filter")
	filtered := make(map[string]DiscoveredProviderInfo)

	for name, info := range discovered {
		// Check include patterns
		if len(source.Spec.Filters.IncludePatterns) > 0 {
			matched := false
			for _, pattern := range source.Spec.Filters.IncludePatterns {
				if ok, err := regexp.MatchString(pattern, name); err == nil && ok {
					matched = true
					break
				}
			}
			if !matched {
				logger.V(1).Info("Provider excluded by include filter", "provider", name)
				continue
			}
		}

		// Check exclude patterns
		excluded := false
		for _, pattern := range source.Spec.Filters.ExcludePatterns {
			if ok, err := regexp.MatchString(pattern, name); err == nil && ok {
				excluded = true
				break
			}
		}
		if excluded {
			logger.V(1).Info("Provider excluded by exclude filter", "provider", name)
			continue
		}

		filtered[name] = info
	}

	// Apply max providers limit
	if source.Spec.Filters.MaxProviders != nil && int32(len(filtered)) > *source.Spec.Filters.MaxProviders {
		truncated := make(map[string]DiscoveredProviderInfo)
		count := int32(0)
		for name, info := range filtered {
			if count >= *source.Spec.Filters.MaxProviders {
				break
			}
			truncated[name] = info
			count++
		}
		return truncated
	}

	return filtered
}

// reconcileDelete handles discovery source deletion
func (r *MCPDiscoverySourceReconciler) reconcileDelete(ctx context.Context, source *mcpv1alpha1.MCPDiscoverySource) (ctrl.Result, error) {
	logger := log.FromContext(ctx)
	logger.Info("Handling deletion for MCPDiscoverySource")

	// Delete all MCPProviders managed by this source
	providerList := &mcpv1alpha1.MCPProviderList{}
	if err := r.List(ctx, providerList,
		client.InNamespace(source.Namespace),
		client.MatchingLabels{LabelDiscoveryManagedBy: source.Name},
	); err != nil {
		logger.Error(err, "Failed to list managed providers for cleanup")
	} else {
		for i := range providerList.Items {
			existing := &providerList.Items[i]
			logger.Info("Deleting managed provider", "provider", existing.Name)
			if err := r.Delete(ctx, existing); err != nil && !errors.IsNotFound(err) {
				logger.Error(err, "Failed to delete managed provider", "provider", existing.Name)
			}
		}
	}

	// Clear metrics
	metrics.ClearDiscoveryMetrics(source.Namespace, source.Name)

	// Remove finalizer
	controllerutil.RemoveFinalizer(source, finalizerName)
	if err := r.Update(ctx, source); err != nil {
		return ctrl.Result{}, err
	}

	r.Recorder.Event(source, corev1.EventTypeNormal, ReasonDeleted, "Discovery source deleted")
	logger.Info("MCPDiscoverySource deleted successfully")

	return ctrl.Result{}, nil
}

// SetupWithManager sets up the controller with the Manager
func (r *MCPDiscoverySourceReconciler) SetupWithManager(mgr ctrl.Manager) error {
	return ctrl.NewControllerManagedBy(mgr).
		For(&mcpv1alpha1.MCPDiscoverySource{}).
		Owns(&mcpv1alpha1.MCPProvider{}).
		Complete(r)
}

// hasRequiredAnnotations checks if all required annotations are present
func hasRequiredAnnotations(annotations map[string]string, required []string) bool {
	if annotations == nil {
		return false
	}
	for _, req := range required {
		if _, ok := annotations[req]; !ok {
			return false
		}
	}
	return true
}

// matchesExpressions checks if labels match label selector requirements
func matchesExpressions(labels map[string]string, requirements []metav1.LabelSelectorRequirement) bool {
	for _, req := range requirements {
		val, exists := labels[req.Key]
		switch req.Operator {
		case metav1.LabelSelectorOpIn:
			if !exists {
				return false
			}
			found := false
			for _, v := range req.Values {
				if val == v {
					found = true
					break
				}
			}
			if !found {
				return false
			}
		case metav1.LabelSelectorOpNotIn:
			if exists {
				for _, v := range req.Values {
					if val == v {
						return false
					}
				}
			}
		case metav1.LabelSelectorOpExists:
			if !exists {
				return false
			}
		case metav1.LabelSelectorOpDoesNotExist:
			if exists {
				return false
			}
		}
	}
	return true
}
