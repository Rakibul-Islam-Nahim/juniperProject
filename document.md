# Automated MicroVM-Based Network Lab Platform
## Complete Project Plan & Technical Design

---

## 1. Project Overview

The goal of this project is to build an automated network-lab platform where a backend service can create, configure, launch, monitor, and destroy isolated network labs on demand.

Each lab will run inside its own **Cloud Hypervisor microVM**. The microVM will be created from a pre-configured **golden image** containing the required operating system, Docker, ContainerLab, vrnetlab, and required Juniper images/files.

The user will only need to request a specific lab type. The backend will automatically:

1. Select the correct golden image.
2. Allocate networking resources through IPAM.
3. Configure the microVM networking.
4. Launch the microVM using Cloud Hypervisor.
5. Start ContainerLab inside the microVM.
6. Launch the required Juniper router/switch topology.
7. Monitor the Juniper devices while they boot.
8. Detect when the complete lab is ready.
9. Notify the platform that the lab is ready for the student.
10. Provide access to the lab terminal.
11. Release the VM and IP resources when the lab is terminated.

The main objective is to make lab creation **fully automated, reproducible, isolated, and scalable**.

---

# 2. Main Project Goal

The platform should transform this:

```text
Student requests:
"Create OSPF Lab"
```

into this:

```text
Backend
   ↓
Allocate IP/network
   ↓
Select OSPF Golden Image
   ↓
Launch Cloud Hypervisor microVM
   ↓
Boot microVM
   ↓
Start Lab Agent
   ↓
Start ContainerLab
   ↓
Launch Juniper topology
   ↓
Wait for Junos devices
   ↓
Verify lab health
   ↓
LAB_READY
   ↓
Student receives terminal access
```

The student should not need to manually:

- create a VM
- configure IP addresses
- install Docker
- install ContainerLab
- configure networking
- start Juniper
- wait manually for the topology
- determine whether the lab is ready

All of these operations should be handled automatically.

---

# 3. Core Architecture

The system will consist of the following major components:

```text
                         Student
                            │
                            ▼
                     Lab Platform UI
                            │
                            ▼
                     Backend API
                            │
              ┌─────────────┼─────────────┐
              │             │             │
              ▼             ▼             ▼
             IPAM       Lab Manager     Database
              │             │
              │             ▼
              │       Golden Images
              │             │
              │             ▼
              │      Cloud Hypervisor
              │             │
              │             ▼
              │          microVM
              │             │
              │        Lab Agent
              │             │
              │        ContainerLab
              │             │
              │      ┌──────┼──────┐
              │      ▼      ▼      ▼
              │     R1     R2     SW1
              │
              └──── Network Allocation

                         │
                         ▼
                  Terminal Gateway
                         │
                         ▼
                      Student
```

---

# 4. Component Responsibilities

## 4.1 Backend Agent

The Backend Agent is the central orchestrator.

It will expose APIs such as:

```text
POST   /labs
GET    /labs/{lab_id}
DELETE /labs/{lab_id}
GET    /labs/{lab_id}/status
```

Its responsibilities include:

- accepting lab creation requests
- validating configuration
- selecting the appropriate golden image
- requesting IP/network resources from IPAM
- generating VM configuration
- launching Cloud Hypervisor
- tracking VM state
- communicating with the Lab Agent
- monitoring lab readiness
- exposing lab status to the frontend
- handling failures
- terminating labs
- releasing allocated resources

The Backend Agent should not directly manage every internal operation of ContainerLab. Instead, it should communicate with a **Lab Agent inside the microVM**.

---

# 5. IPAM

## 5.1 Purpose

IPAM means **IP Address Management**.

The IPAM component will automatically allocate network resources to each lab.

The purpose is to prevent:

- duplicate IP addresses
- overlapping management networks
- manual network configuration
- conflicts between concurrent labs

For example:

```text
Lab 1001 → 172.30.1.0/24
Lab 1002 → 172.30.2.0/24
Lab 1003 → 172.30.3.0/24
```

Each lab receives its own network allocation.

---

## 5.2 IPAM Responsibilities

IPAM will manage:

- lab subnet
- microVM management IP
- gateway
- optional device management IPs
- allocated address ranges
- resource ownership
- resource release

Example:

```text
Lab ID: lab-1001

Subnet:
172.30.1.0/24

Gateway:
172.30.1.1

microVM:
172.30.1.10

Optional management addresses:
172.30.1.20
172.30.1.21
172.30.1.22
```

The exact addressing scheme will be finalized during the networking implementation phase.

---

# 6. Golden Image Architecture

Each lab type will have its own golden image.

Example:

```text
Golden Images
│
├── ospf.qcow2
├── bgp.qcow2
├── vlan.qcow2
├── switching.qcow2
└── enterprise.qcow2
```

Each golden image will already contain the software and files required by that lab.

Typical contents:

```text
Operating System
├── Docker
├── ContainerLab
├── vrnetlab
├── Juniper image
├── Lab Agent
├── required configuration
└── required scripts
```

Therefore, launching a new lab does not require reinstalling the entire environment.

---

# 7. Lab Type → Golden Image Mapping

The Backend Agent will maintain a mapping between the requested lab type and its golden image.

Example:

```text
Lab Type        Golden Image

OSPF            ospf.qcow2
BGP             bgp.qcow2
VLAN            vlan.qcow2
Switching       switching.qcow2
Enterprise      enterprise.qcow2
```

When the user requests:

```json
{
  "lab_type": "ospf"
}
```

the backend selects:

```text
ospf.qcow2
```

and launches it.

This makes the system easy to extend.

Adding a new lab type should primarily require:

1. Creating the golden image.
2. Defining its ContainerLab topology.
3. Registering the lab type in the backend.

---

# 8. Cloud Hypervisor

Cloud Hypervisor will be responsible for running the isolated microVM.

The Backend Agent will prepare the required configuration and start Cloud Hypervisor.

Conceptually:

```text
Backend Agent
     │
     │ VM configuration
     ▼
Cloud Hypervisor
     │
     ▼
microVM
```

The configuration will include:

- kernel
- disk image
- memory
- CPU count
- network interface
- MAC address
- TAP interface
- API socket
- console configuration
- required boot parameters

---

# 9. MicroVM Networking

The microVM must automatically receive network connectivity.

No manual configuration should be required after VM creation.

The general architecture will be:

```text
Internet
   │
Host Network
   │
Bridge / Network Layer
   │
TAP Interface
   │
microVM eth0
   │
Lab Agent
```

The Backend Agent will coordinate the required network configuration using information obtained from IPAM.

The microVM should have:

- a unique management IP
- gateway access
- Internet connectivity
- connectivity to the Backend Agent
- connectivity required for terminal access

---

# 10. Separation of Network Layers

The platform will maintain a clear separation between:

### Layer 1 — MicroVM Management Network

Used for:

- Internet access
- backend communication
- SSH
- Lab Agent communication
- terminal access
- monitoring

### Layer 2 — ContainerLab Topology Network

Used for:

- router-to-router connections
- router-to-switch connections
- OSPF
- BGP
- VLAN
- routing experiments
- other network-lab traffic

Conceptually:

```text
                microVM
                   │
          Management Network
                   │
          Internet / Backend
                   
                   +

             ContainerLab
                   │
          ┌────────┼────────┐
          ▼        ▼        ▼
         R1       R2       SW1
          │        │
          └────────┘
       Lab Topology
```

The Juniper devices do not need public IP addresses.

Only the required management interfaces should have external connectivity.

---

# 11. Lab Agent Inside the microVM

A small **Lab Agent** will run inside every microVM.

Its purpose is to manage the internal lab environment.

Architecture:

```text
Backend Agent
      │
      │ API
      ▼
  Lab Agent
      │
      ├── Docker
      ├── ContainerLab
      └── Juniper Devices
```

The Lab Agent will be responsible for:

- starting ContainerLab
- starting the correct topology
- checking ContainerLab status
- checking Juniper devices
- monitoring device boot status
- reporting health to the Backend Agent
- optionally stopping the lab
- exposing a health/status API

---

# 12. ContainerLab Startup

When the microVM finishes booting:

```text
microVM boot
     ↓
system services start
     ↓
Lab Agent starts
     ↓
Lab Agent loads lab configuration
     ↓
ContainerLab starts
     ↓
Juniper topology starts
```

The topology configuration should already exist in the golden image or be provided by the Backend Agent.

For example:

```text
Lab Type: OSPF

ContainerLab topology:
ospf.clab.yml
```

The Lab Agent executes the appropriate ContainerLab startup procedure.

---

# 13. Juniper Device Startup

ContainerLab will start the required Juniper devices.

Example:

```text
ContainerLab
     │
     ├── r1
     ├── r2
     ├── r3
     └── sw1
```

The Juniper devices may take approximately **6–7 minutes** to become fully operational.

Therefore, VM startup and lab readiness must be treated as two separate states.

---

# 14. Lab Readiness Detection

The system must not consider the lab ready simply because the Cloud Hypervisor process started.

The lifecycle should be:

```text
VM_STARTED
    ↓
VM_READY
    ↓
CONTAINERLAB_STARTING
    ↓
DEVICES_BOOTING
    ↓
DEVICE_HEALTH_CHECK
    ↓
LAB_READY
```

The Backend Agent should only expose the lab to the student after the final health check succeeds.

---

# 15. Health Check Strategy

The platform should use active health checks rather than a fixed sleep.

Bad approach:

```text
Start Juniper
↓
sleep(420)
↓
Assume ready
```

Preferred approach:

```text
Start Juniper
↓
Check device
↓
Not ready
↓
Wait
↓
Check again
↓
Not ready
↓
Wait
↓
Check again
↓
Ready
```

A maximum timeout should also exist.

For example:

```text
Maximum startup timeout = configurable
```

If the device does not become ready within the timeout:

```text
LAB_STARTUP_TIMEOUT
```

The backend can then mark the lab as failed and perform cleanup.

---

# 16. Lab State Machine

Each lab should have a clearly defined lifecycle.

### Creation

```text
REQUESTED
    ↓
CREATING
    ↓
NETWORK_ALLOCATED
    ↓
VM_STARTING
    ↓
VM_READY
    ↓
CONTAINERLAB_STARTING
    ↓
DEVICES_BOOTING
    ↓
LAB_READY
```

### Failure

Any stage can transition to:

```text
FAILED
```

Examples:

```text
VM_START_FAILED
CONTAINERLAB_FAILED
DEVICE_BOOT_FAILED
HEALTH_CHECK_TIMEOUT
NETWORK_CONFIGURATION_FAILED
```

### Termination

```text
LAB_READY
    ↓
STOPPING
    ↓
VM_STOPPED
    ↓
RESOURCES_RELEASED
    ↓
DESTROYED
```

This state machine will make the platform much easier to monitor and debug.

---

# 17. Asynchronous Lab Creation

Lab creation should be asynchronous.

The initial API request should not remain open for 6–7 minutes.

Example:

```http
POST /labs
```

The backend immediately returns:

```json
{
  "lab_id": "lab-1001",
  "status": "CREATING"
}
```

The frontend can then query:

```http
GET /labs/lab-1001
```

Example response:

```json
{
  "lab_id": "lab-1001",
  "status": "DEVICES_BOOTING",
  "progress": 80
}
```

When the lab is ready:

```json
{
  "lab_id": "lab-1001",
  "status": "READY"
}
```

The frontend can then provide terminal access.

---

# 18. Proposed API

## Create Lab

```http
POST /api/v1/labs
```

Example:

```json
{
  "lab_type": "ospf",
  "cpu": 8,
  "memory": "16G"
}
```

Response:

```json
{
  "lab_id": "lab-1001",
  "status": "CREATING"
}
```

---

## Get Lab Status

```http
GET /api/v1/labs/{lab_id}
```

Response:

```json
{
  "lab_id": "lab-1001",
  "lab_type": "ospf",
  "status": "LAB_READY"
}
```

---

## Delete Lab

```http
DELETE /api/v1/labs/{lab_id}
```

The backend should:

```text
Stop ContainerLab
      ↓
Stop microVM
      ↓
Remove temporary resources
      ↓
Release IPAM allocation
      ↓
Update database
```

---

# 19. Backend Database

A database should store the state of every lab.

Example record:

```text
lab_id
user_id
lab_type
golden_image
status
cpu
memory
subnet
management_ip
gateway
vm_pid
vm_api_socket
created_at
started_at
ready_at
terminated_at
error
```

This allows the backend to recover information even if the service restarts.

---

# 20. Terminal Access

After the lab becomes ready, the platform must provide terminal access.

The expected flow is:

```text
Student Browser
      │
      ▼
Lab Portal
      │
      ▼
Terminal Gateway
      │
      ▼
microVM / ContainerLab
      │
      ▼
Juniper Console / SSH
```

The Terminal Gateway will bridge the student's WebSocket connection to the appropriate lab console or SSH endpoint.

The student should not need direct network access to the microVM.

---

# 21. Internet Access

The microVM should be able to access the Internet automatically.

This is required for:

- package access if needed
- external connectivity
- lab-related services
- management
- monitoring

The architecture should use the host's networking layer to provide outbound connectivity.

The Juniper topology itself should remain isolated as much as practical.

---

# 22. Resource Management

Because each lab runs inside its own microVM, the Backend Agent must track resource usage.

Example:

```text
Lab 1 → 4 vCPU + 8 GB RAM
Lab 2 → 4 vCPU + 8 GB RAM
Lab 3 → 4 vCPU + 8 GB RAM
```

If all three run simultaneously, the host must have sufficient resources for all three workloads.

Therefore, the Backend Agent should eventually perform:

```text
Resource check
     ↓
Enough CPU/RAM?
     │
   ┌─┴─┐
  YES  NO
   │    │
Launch  Reject/Queue
```

This will prevent overcommitting the bare-metal server.

---

# 23. Lab Cleanup

Every lab should have a complete cleanup workflow.

When the student finishes:

```text
DELETE /labs/{lab_id}
```

The backend performs:

```text
Stop ContainerLab
       ↓
Stop microVM
       ↓
Delete runtime resources
       ↓
Delete TAP/network resources
       ↓
Release IPAM allocation
       ↓
Remove temporary overlay
       ↓
Update database
```

The golden image must remain untouched.

---

# 24. Golden Image Protection

The golden image should be treated as immutable.

The recommended model is:

```text
Golden Image
     │
     │ copy / overlay
     ▼
Lab-specific runtime disk
     │
     ▼
microVM
```

The student lab can modify its own runtime environment without modifying the original golden image.

Therefore:

```text
Golden Image
     │
     ├── Lab 1001 overlay
     ├── Lab 1002 overlay
     └── Lab 1003 overlay
```

This makes the same golden image reusable for many labs.

---

# 25. Failure Handling

The backend must handle failures at every stage.

Example:

### IPAM failure

```text
IP allocation failed
↓
Do not start VM
↓
Return creation failure
```

### Cloud Hypervisor failure

```text
VM failed
↓
Mark lab FAILED
↓
Release IP
↓
Cleanup resources
```

### ContainerLab failure

```text
ContainerLab failed
↓
Mark lab FAILED
↓
Stop VM
↓
Release IP
```

### Juniper boot timeout

```text
Device not ready
↓
Timeout
↓
Mark lab FAILED
↓
Cleanup
```

The system should never leave partially created labs consuming resources.

---

# 26. Monitoring

The platform should eventually monitor:

### Backend

- API latency
- request count
- lab creation failures
- lab creation time

### Cloud Hypervisor

- VM count
- CPU
- memory
- VM startup failures

### microVM

- CPU
- memory
- disk
- network

### ContainerLab

- topology status
- container status

### Juniper

- device boot status
- SSH/console availability
- device health

Monitoring can later be integrated with:

```text
Prometheus
Grafana
Sentry
```

---

# 27. Logging

Each lab should have an identifiable log context.

Example:

```text
lab_id = lab-1001
```

All relevant logs should include the lab ID.

Example:

```text
[lab-1001] Allocating subnet
[lab-1001] Starting Cloud Hypervisor
[lab-1001] microVM ready
[lab-1001] Starting ContainerLab
[lab-1001] r1 booting
[lab-1001] r2 booting
[lab-1001] All devices ready
[lab-1001] LAB_READY
```

This makes debugging multiple simultaneous labs significantly easier.

---

# 28. Security and Isolation

The platform should maintain isolation between labs.

Each lab should have:

- separate microVM
- separate runtime disk/overlay
- separate management network allocation
- separate ContainerLab topology
- separate terminal session
- separate lifecycle

For example:

```text
Student A
   ↓
microVM A
   ↓
Lab A

Student B
   ↓
microVM B
   ↓
Lab B
```

Student A should not be able to access Student B's microVM or topology.

---

# 29. Complete Lab Creation Flow

The complete workflow will be:

```text
1. Student requests a lab
        ↓
2. Backend receives POST /labs
        ↓
3. Validate lab configuration
        ↓
4. Check available CPU/RAM
        ↓
5. Select golden image
        ↓
6. Request subnet/IP from IPAM
        ↓
7. Generate network configuration
        ↓
8. Create runtime disk/overlay
        ↓
9. Create TAP/network resources
        ↓
10. Start Cloud Hypervisor
        ↓
11. Wait for microVM
        ↓
12. Verify microVM networking
        ↓
13. Lab Agent starts
        ↓
14. Backend sends lab configuration
        ↓
15. Lab Agent starts ContainerLab
        ↓
16. ContainerLab launches Juniper topology
        ↓
17. Juniper devices begin booting
        ↓
18. Lab Agent performs health checks
        ↓
19. All required devices become ready
        ↓
20. Lab Agent reports READY
        ↓
21. Backend marks lab LAB_READY
        ↓
22. Backend provides terminal access
        ↓
23. Student starts using lab
```

---

# 30. Complete Lab Destruction Flow

```text
Student finishes lab
        ↓
DELETE /labs/{lab_id}
        ↓
Backend marks STOPPING
        ↓
Stop ContainerLab
        ↓
Stop microVM
        ↓
Remove TAP/network resources
        ↓
Delete runtime overlay
        ↓
Release IPAM allocation
        ↓
Release CPU/RAM
        ↓
Update database
        ↓
LAB_DESTROYED
```

---

# 31. Development Phases

## Phase 1 — IPAM Design

Objectives:

- define IP address pool
- define subnet allocation strategy
- define management network
- implement allocation
- implement release
- prevent duplicate allocation

Deliverable:

```text
Working IPAM service/module
```

---

## Phase 2 — Backend API

Implement:

```text
POST /labs
GET /labs/{id}
DELETE /labs/{id}
```

Implement:

- request validation
- lab state management
- database storage

---

## Phase 3 — Cloud Hypervisor Launcher

Build the backend functionality to:

- select image
- create runtime disk
- configure CPU/RAM
- configure network
- create TAP
- launch Cloud Hypervisor
- track PID/API socket
- detect VM failure

---

## Phase 4 — Automatic MicroVM Networking

Implement:

```text
IPAM
 ↓
Network configuration
 ↓
TAP
 ↓
Bridge
 ↓
microVM
```

Verify:

- microVM receives correct IP
- gateway works
- Internet works
- backend connectivity works

---

## Phase 5 — Lab Agent

Build the internal microVM agent.

Responsibilities:

```text
Start
 ↓
Detect Docker
 ↓
Start ContainerLab
 ↓
Monitor topology
 ↓
Monitor Juniper
 ↓
Report status
```

---

## Phase 6 — ContainerLab Integration

Implement automatic:

```text
Lab Type
 ↓
Topology selection
 ↓
ContainerLab deployment
 ↓
Juniper startup
```

---

## Phase 7 — Juniper Readiness Detection

Implement reliable health checks.

The system must distinguish:

```text
Container exists
```

from:

```text
Junos is actually ready
```

This phase is critical because Juniper boot time is several minutes.

---

## Phase 8 — Backend ↔ Lab Agent Communication

Implement:

```text
Backend
   ↕
Lab Agent
```

Possible operations:

```text
GET /health
GET /status
POST /start
POST /stop
```

The Lab Agent will report:

```text
VM ready
ContainerLab ready
R1 ready
R2 ready
SW1 ready
Lab ready
```

---

## Phase 9 — Terminal Integration

Connect:

```text
Student Browser
      ↓
Terminal Gateway
      ↓
Lab microVM
      ↓
Juniper Console/SSH
```

Only expose the appropriate terminal/session endpoint to the student.

---

## Phase 10 — Cleanup and Resource Reclamation

Implement complete destruction.

Ensure:

- VM stops
- ContainerLab stops
- TAP disappears
- overlay is removed
- IP is released
- database state is updated
- resources become available for another lab

---

# 32. Future Scalability

The initial implementation may run on one bare-metal server.

Later, multiple servers can be supported:

```text
                 Backend
                    │
              Scheduler
                    │
        ┌───────────┼───────────┐
        ▼           ▼           ▼
    BareMetal 1  BareMetal 2  BareMetal 3
        │           │           │
     microVMs    microVMs    microVMs
```

The scheduler can select the host based on:

- available CPU
- available RAM
- number of running labs
- lab type
- image availability
- host health

This allows the platform to scale horizontally.

---

# 33. Final Target Architecture

The final system should conceptually operate like this:

```text
                         ┌───────────────┐
                         │    Student    │
                         └───────┬───────┘
                                 │
                                 ▼
                         ┌───────────────┐
                         │   Lab Portal  │
                         └───────┬───────┘
                                 │
                                 ▼
                    ┌────────────────────────┐
                    │      Backend Agent     │
                    │                        │
                    │ API                    │
                    │ Scheduler              │
                    │ Lab Manager            │
                    │ Resource Manager       │
                    └───────┬───────┬────────┘
                            │       │
                    ┌───────┘       └────────┐
                    ▼                        ▼
              ┌───────────┐           ┌────────────┐
              │    IPAM   │           │  Database  │
              └───────────┘           └────────────┘
                    │
                    ▼
             Network Allocation
                    │
                    ▼
             ┌───────────────┐
             │Cloud Hypervisor│
             └───────┬───────┘
                     │
                     ▼
                ┌─────────┐
                │ microVM │
                │         │
                │  Lab    │
                │  Agent  │
                │    │    │
                │    ▼    │
                │Container│
                │  Lab    │
                │    │    │
                │ ┌──┼──┐ │
                │ ▼  ▼  ▼ │
                │ R1 R2 SW1│
                └─────────┘
                     │
                     ▼
              Terminal Gateway
                     │
                     ▼
                  Student
```

---

# 34. Key Design Principles

The implementation should follow these principles:

### 1. Golden images are immutable

Never modify the master golden image during normal lab execution.

### 2. Each lab is isolated

Every lab gets its own microVM and runtime environment.

### 3. IP allocation is automatic

No manual IP assignment.

### 4. Lab creation is asynchronous

The API should return immediately with a lab ID.

### 5. Readiness is health-based

Do not assume the lab is ready simply because the VM started.

### 6. Every resource has an owner

IP addresses, VM processes, disks, TAP interfaces, and labs should all be associated with a unique `lab_id`.

### 7. Every failure must trigger cleanup

A failed lab should not leave resources behind.

### 8. Backend is the orchestrator

Cloud Hypervisor, IPAM, and Lab Agent should each have clear responsibilities.

### 9. Lab Agent handles internal VM operations

The Backend Agent should not need to directly execute every ContainerLab command.

### 10. Design for multiple labs from the beginning

Even if the first test uses one lab, the architecture should support concurrent labs.

---

# 35. MVP Definition

The first working version does not need every advanced feature.

The MVP should accomplish:

```text
POST /labs
      ↓
Select golden image
      ↓
Allocate IP
      ↓
Launch Cloud Hypervisor
      ↓
Boot microVM
      ↓
Start ContainerLab
      ↓
Start Juniper topology
      ↓
Detect Juniper ready
      ↓
Return LAB_READY
      ↓
Student accesses terminal
      ↓
DELETE /labs/{id}
      ↓
Cleanup everything
```

If this complete workflow works reliably, the core project is successful.

---

# 36. Final Project Objective

The final objective is to create a platform where **network labs become disposable, isolated, and automatically provisioned resources**.

Instead of manually preparing:

```text
VM
Docker
ContainerLab
Juniper
Networking
IP addresses
Terminal
```

the user simply requests:

```text
Create Lab: OSPF
```

and the platform performs:

```text
IP Allocation
      ↓
Golden Image Selection
      ↓
MicroVM Provisioning
      ↓
Automatic Networking
      ↓
ContainerLab Deployment
      ↓
Juniper Boot
      ↓
Health Verification
      ↓
Terminal Availability
      ↓
LAB_READY
```

When the student finishes:

```text
Destroy Lab
      ↓
VM destroyed
      ↓
Network removed
      ↓
IP released
      ↓
Resources released
```

The resulting architecture provides a strong foundation for a **multi-user, isolated, automated network-lab platform based on Cloud Hypervisor + microVM + ContainerLab + Juniper + IPAM**.