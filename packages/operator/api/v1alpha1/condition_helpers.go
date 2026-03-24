package v1alpha1

import (
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
)

// SetConditionOnSlice sets or updates a condition in the given slice.
// If a condition with the same type already exists, it is updated in-place.
// LastTransitionTime is only updated when the status actually changes.
func SetConditionOnSlice(conditions *[]Condition, condType string, status metav1.ConditionStatus, reason, message string) {
	now := metav1.Now()

	for i, c := range *conditions {
		if c.Type == condType {
			if c.Status != status {
				(*conditions)[i].LastTransitionTime = now
			}
			(*conditions)[i].Status = status
			(*conditions)[i].Reason = reason
			(*conditions)[i].Message = message
			return
		}
	}

	*conditions = append(*conditions, Condition{
		Type:               condType,
		Status:             status,
		LastTransitionTime: now,
		Reason:             reason,
		Message:            message,
	})
}

// GetConditionFromSlice returns the condition with the given type, or nil.
func GetConditionFromSlice(conditions []Condition, condType string) *Condition {
	for i := range conditions {
		if conditions[i].Type == condType {
			return &conditions[i]
		}
	}
	return nil
}
