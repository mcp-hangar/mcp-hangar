### Several pods sharing one `mcp-hangar.io/name` register as one server

Pods that carry the same `mcp-hangar.io/name` annotation, which is every replica of a Deployment, used to be discovered as separate entries under one name. The gateway tracks a server by name, so they replaced each other each cycle and raised a config-change event per swap. Discovery now registers one pod per name, the one that sorts first by namespace and pod name, and logs `kubernetes_discovery_several_pods_for_one_name` listing the rest.

This is not load balancing. For a workload with several replicas, put a Service in front, point the registration at it and give it its own name, instead of annotating the pods. The same name in two namespaces is treated the same way.
