// Package controller implements Kubernetes controllers for MCP resources
package controller

import (
	"context"
	"fmt"
	"time"

	"k8s.io/apimachinery/pkg/api/errors"
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
	"k8s.io/apimachinery/pkg/labels"
	"k8s.io/apimachinery/pkg/runtime"
	"k8s.io/apimachinery/pkg/types"
	"k8s.io/client-go/tools/record"
	ctrl "sigs.k8s.io/controller-runtime"
	"sigs.k8s.io/controller-runtime/pkg/client"
	"sigs.k8s.io/controller-runtime/pkg/controller/controllerutil"
	"sigs.k8s.io/controller-runtime/pkg/handler"
	"sigs.k8s.io/controller-runtime/pkg/log"
	"sigs.k8s.io/controller-runtime/pkg/reconcile"

	mcpv1alpha1 "github.com/mcp-hangar/mcp-hangar/operator/api/v1alpha1"
	"github.com/mcp-hangar/mcp-hangar/operator/pkg/hangar"
	"github.com/mcp-hangar/mcp-hangar/operator/pkg/metrics"
)

// MCPProviderGroupReconciler reconciles a MCPProviderGroup object.
// It is a read-only aggregation controller: it selects MCPProviders by label
// selector, counts provider states, evaluates health policy thresholds, and
// reports Ready/Degraded/Available conditions on the group status subresource.
type MCPProviderGroupReconciler struct {
	client.Client
	Scheme       *runtime.Scheme
	Recorder     record.EventRecorder
	HangarClient *hangar.Client // Optional, nil-checked
}

// +kubebuilder:rbac:groups=mcp-hangar.io,resources=mcpprovidergroups,verbs=get;list;watch;create;update;patch;delete
// +kubebuilder:rbac:groups=mcp-hangar.io,resources=mcpprovidergroups/status,verbs=get;update;patch
// +kubebuilder:rbac:groups=mcp-hangar.io,resources=mcpprovidergroups/finalizers,verbs=update
// +kubebuilder:rbac:groups=mcp-hangar.io,resources=mcpproviders,verbs=get;list;watch
// +kubebuilder:rbac:groups="",resources=events,verbs=create;patch

// Reconcile performs the reconciliation loop for MCPProviderGroup
func (r *MCPProviderGroupReconciler) Reconcile(ctx context.Context, req ctrl.Request) (ctrl.Result, error) {
	logger := log.FromContext(ctx)
	startTime := time.Now()

	logger.Info("Reconciling MCPProviderGroup", "namespacedName", req.NamespacedName)
	defer func() {
		duration := time.Since(startTime)
		metrics.ReconcileDuration.WithLabelValues("mcpprovidergroup").Observe(duration.Seconds())
	}()

	// Fetch the MCPProviderGroup instance
	group := &mcpv1alpha1.MCPProviderGroup{}
	if err := r.Get(ctx, req.NamespacedName, group); err != nil {
		if errors.IsNotFound(err) {
			logger.Info("MCPProviderGroup resource not found, ignoring")
			return ctrl.Result{}, nil
		}
		logger.Error(err, "Failed to get MCPProviderGroup")
		metrics.ReconcileTotal.WithLabelValues("mcpprovidergroup", "error").Inc()
		return ctrl.Result{}, err
	}

	// Handle deletion
	if !group.ObjectMeta.DeletionTimestamp.IsZero() {
		result, err := r.reconcileDelete(ctx, group)
		if err != nil {
			metrics.ReconcileTotal.WithLabelValues("mcpprovidergroup", "error").Inc()
		} else {
			metrics.ReconcileTotal.WithLabelValues("mcpprovidergroup", "success").Inc()
		}
		return result, err
	}

	// Add finalizer if not present
	if !controllerutil.ContainsFinalizer(group, finalizerName) {
		controllerutil.AddFinalizer(group, finalizerName)
		if err := r.Update(ctx, group); err != nil {
			return ctrl.Result{}, err
		}
		return ctrl.Result{Requeue: true}, nil
	}

	// Main reconciliation logic
	result, err := r.reconcileNormal(ctx, group)
	if err != nil {
		metrics.ReconcileTotal.WithLabelValues("mcpprovidergroup", "error").Inc()
	} else {
		metrics.ReconcileTotal.WithLabelValues("mcpprovidergroup", "success").Inc()
	}

	return result, err
}

// reconcileNormal handles normal (non-deletion) reconciliation for groups
func (r *MCPProviderGroupReconciler) reconcileNormal(ctx context.Context, group *mcpv1alpha1.MCPProviderGroup) (ctrl.Result, error) {
	logger := log.FromContext(ctx)

	// Update observed generation if changed
	if group.Status.ObservedGeneration != group.Generation {
		group.Status.ObservedGeneration = group.Generation
	}

	// Convert label selector
	if group.Spec.Selector == nil {
		group.Status.SetCondition(ConditionReady, metav1.ConditionUnknown, "NoSelector", "No label selector defined")
		if err := r.Status().Update(ctx, group); err != nil {
			return ctrl.Result{}, err
		}
		return ctrl.Result{}, nil
	}

	selector, err := metav1.LabelSelectorAsSelector(group.Spec.Selector)
	if err != nil {
		logger.Error(err, "Failed to parse label selector")
		group.Status.SetCondition(ConditionReady, metav1.ConditionFalse, "InvalidSelector", fmt.Sprintf("Invalid label selector: %v", err))
		if err := r.Status().Update(ctx, group); err != nil {
			return ctrl.Result{}, err
		}
		return ctrl.Result{}, nil
	}

	// List MCPProviders matching selector in same namespace
	providerList := &mcpv1alpha1.MCPProviderList{}
	listOpts := &client.ListOptions{
		Namespace:     group.Namespace,
		LabelSelector: selector,
	}
	if err := r.List(ctx, providerList, listOpts); err != nil {
		logger.Error(err, "Failed to list MCPProviders")
		return ctrl.Result{RequeueAfter: errorRequeueAfter}, err
	}

	// Aggregate status counts
	var readyCount, degradedCount, coldCount, deadCount int32
	memberStatuses := make([]mcpv1alpha1.ProviderMemberStatus, 0, len(providerList.Items))

	for i := range providerList.Items {
		p := &providerList.Items[i]
		member := mcpv1alpha1.ProviderMemberStatus{
			Name:            p.Name,
			Namespace:       p.Namespace,
			State:           string(p.Status.State),
			LastHealthCheck: p.Status.LastHealthCheck,
		}
		memberStatuses = append(memberStatuses, member)

		switch p.Status.State {
		case mcpv1alpha1.ProviderStateReady:
			readyCount++
		case mcpv1alpha1.ProviderStateDegraded:
			degradedCount++
		case mcpv1alpha1.ProviderStateCold:
			coldCount++
		case mcpv1alpha1.ProviderStateDead:
			deadCount++
		default:
			// Initializing or empty state treated as cold
			coldCount++
		}
	}

	// Populate status fields
	group.Status.ProviderCount = int32(len(providerList.Items))
	group.Status.ReadyCount = readyCount
	group.Status.DegradedCount = degradedCount
	group.Status.ColdCount = coldCount
	group.Status.DeadCount = deadCount
	group.Status.Providers = memberStatuses
	group.Status.ActiveStrategy = string(group.Spec.Strategy)

	// Evaluate conditions
	r.evaluateConditions(group)

	// Update metrics
	metrics.GroupProviderCount.WithLabelValues(group.Namespace, group.Name, "Ready").Set(float64(readyCount))
	metrics.GroupProviderCount.WithLabelValues(group.Namespace, group.Name, "Degraded").Set(float64(degradedCount))
	metrics.GroupProviderCount.WithLabelValues(group.Namespace, group.Name, "Cold").Set(float64(coldCount))
	metrics.GroupProviderCount.WithLabelValues(group.Namespace, group.Name, "Dead").Set(float64(deadCount))

	// Update status subresource
	if err := r.Status().Update(ctx, group); err != nil {
		logger.Error(err, "Failed to update MCPProviderGroup status")
		return ctrl.Result{}, err
	}

	logger.Info("MCPProviderGroup reconciled",
		"providerCount", group.Status.ProviderCount,
		"readyCount", readyCount,
		"degradedCount", degradedCount,
		"coldCount", coldCount,
		"deadCount", deadCount,
	)

	return ctrl.Result{RequeueAfter: readyRequeueAfter}, nil
}

// evaluateConditions sets Ready, Degraded, and Available conditions based on
// provider counts and health policy thresholds.
func (r *MCPProviderGroupReconciler) evaluateConditions(group *mcpv1alpha1.MCPProviderGroup) {
	status := &group.Status

	// Zero-member groups
	if status.ProviderCount == 0 {
		status.SetCondition(ConditionReady, metav1.ConditionUnknown, "NoProviders", "No providers match selector")
		status.SetCondition(ConditionAvailable, metav1.ConditionFalse, "NoProviders", "No providers match selector")
		status.SetCondition(ConditionDegraded, metav1.ConditionFalse, "NoProviders", "No providers match selector")
		return
	}

	// Available: at least 1 ready provider can serve traffic
	if status.ReadyCount > 0 {
		status.SetCondition(ConditionAvailable, metav1.ConditionTrue, "ProvidersAvailable",
			fmt.Sprintf("%d provider(s) available", status.ReadyCount))
	} else {
		status.SetCondition(ConditionAvailable, metav1.ConditionFalse, "NoReadyProviders", "No ready providers")
	}

	// Ready: health policy threshold met via IsHealthy helper
	if status.IsHealthy(group.Spec.HealthPolicy) {
		status.SetCondition(ConditionReady, metav1.ConditionTrue, "HealthyThresholdMet",
			fmt.Sprintf("%d/%d providers ready", status.ReadyCount, status.ProviderCount))
	} else {
		status.SetCondition(ConditionReady, metav1.ConditionFalse, "HealthyThresholdNotMet",
			fmt.Sprintf("%d/%d providers ready, threshold not met", status.ReadyCount, status.ProviderCount))
	}

	// Degraded: any unhealthy providers exist (can coexist with Ready)
	unhealthyCount := status.DegradedCount + status.DeadCount
	if unhealthyCount > 0 {
		status.SetCondition(ConditionDegraded, metav1.ConditionTrue, "UnhealthyProviders",
			fmt.Sprintf("%d provider(s) unhealthy (%d degraded, %d dead)", unhealthyCount, status.DegradedCount, status.DeadCount))
	} else {
		status.SetCondition(ConditionDegraded, metav1.ConditionFalse, "AllHealthy", "All providers healthy")
	}
}

// reconcileDelete handles group deletion by cleaning up the finalizer and metrics
func (r *MCPProviderGroupReconciler) reconcileDelete(ctx context.Context, group *mcpv1alpha1.MCPProviderGroup) (ctrl.Result, error) {
	logger := log.FromContext(ctx)
	logger.Info("Handling deletion for MCPProviderGroup")

	// Clear group metrics
	metrics.ClearGroupMetrics(group.Namespace, group.Name)

	// Remove finalizer
	controllerutil.RemoveFinalizer(group, finalizerName)
	if err := r.Update(ctx, group); err != nil {
		return ctrl.Result{}, err
	}

	r.Recorder.Event(group, "Normal", ReasonDeleted, "Provider group deleted")
	logger.Info("MCPProviderGroup deleted successfully")

	return ctrl.Result{}, nil
}

// findGroupsForProvider returns reconcile requests for all MCPProviderGroups
// whose label selector matches the given MCPProvider. This ensures groups
// re-reconcile when matching providers change state.
func (r *MCPProviderGroupReconciler) findGroupsForProvider(ctx context.Context, obj client.Object) []reconcile.Request {
	logger := log.FromContext(ctx)

	provider, ok := obj.(*mcpv1alpha1.MCPProvider)
	if !ok {
		return nil
	}

	// List all groups in the provider's namespace
	groupList := &mcpv1alpha1.MCPProviderGroupList{}
	if err := r.List(ctx, groupList, client.InNamespace(provider.Namespace)); err != nil {
		logger.Error(err, "Failed to list MCPProviderGroups for provider mapping")
		return nil
	}

	var requests []reconcile.Request
	providerLabels := labels.Set(provider.Labels)

	for i := range groupList.Items {
		group := &groupList.Items[i]

		if group.Spec.Selector == nil {
			continue
		}

		selector, err := metav1.LabelSelectorAsSelector(group.Spec.Selector)
		if err != nil {
			logger.Error(err, "Failed to parse group selector", "group", group.Name)
			continue
		}

		if selector.Matches(providerLabels) {
			requests = append(requests, reconcile.Request{
				NamespacedName: types.NamespacedName{
					Name:      group.Name,
					Namespace: group.Namespace,
				},
			})
		}
	}

	return requests
}

// SetupWithManager sets up the controller with the Manager
func (r *MCPProviderGroupReconciler) SetupWithManager(mgr ctrl.Manager) error {
	return ctrl.NewControllerManagedBy(mgr).
		For(&mcpv1alpha1.MCPProviderGroup{}).
		Watches(
			&mcpv1alpha1.MCPProvider{},
			handler.EnqueueRequestsFromMapFunc(r.findGroupsForProvider),
		).
		Complete(r)
}
