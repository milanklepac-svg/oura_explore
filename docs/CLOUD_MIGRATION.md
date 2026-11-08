# PC-0026 — public hosting decision

Reviewed 8 November 2026. Research only: no account, billing subscription or deployment created. The application remains local.

## Choice

For the existing requirement of no additional monthly spend, **Oracle Always Free is the closest public-hosting fit**: one ARM VM can run our existing containers, PostgreSQL and scheduled workers. This is portable container hosting on a VM, not a managed serverless platform. Capacity shortages and idle-instance reclamation make it unsuitable for a promise of uninterrupted availability. Keep the tenancy unupgraded and select only eligible resources. [Oracle allowances and reclamation policy](https://docs.oracle.com/en-us/iaas/Content/FreeTier/freetier_topic-Always_Free_Resources.htm), [signup and payment verification](https://docs.oracle.com/en-us/iaas/Content/GSG/Tasks/signingup_topic-Sign_Up_for_Free_Oracle_Cloud_Promotion.htm).

For a **managed cloud-native architecture**, prefer **Cloud Run + Neon PostgreSQL**. This recommendation is an architectural judgment: stateless containers, scale-to-zero HTTP service and finite scheduled provider jobs suit this small application. It is not a guaranteed $0 offer for the complete stack. Billing is required, and traffic, job runtime, registry/build storage, secrets, logs and backups must all be accounted for. Do not activate it under the current zero-spend requirement without an explicit decision accepting that exposure. [Cloud Run pricing](https://cloud.google.com/run/pricing), [Google free program](https://docs.cloud.google.com/free/docs/free-cloud-features).

| Candidate | Ongoing allowance | Fit and tradeoff |
|---|---|---|
| Oracle Always Free | A1: 1,500 OCPU-hours and 9,000 GB-hours/month, equivalent to 2 OCPU/12 GB; 200 GB combined boot/block storage | Fewest application changes; self-managed HTTPS, patching, PostgreSQL and backups. Free home-region capacity may be unavailable; idle VM can be reclaimed. |
| Cloud Run + Neon | Request-based service: 180,000 vCPU-seconds, 360,000 GiB-seconds and 2 million requests/month. Neon Free: 1 GB, 100 CU-hours and 5 GB transfer/project/month, no card. | Managed, scale-to-zero architecture. Jobs have their own compute pricing; do not count service and job grants as independent free budgets. Provider overages and ancillary services can be billed. Neon quota exhaustion suspends compute or blocks storage growth. |
| Azure Container Apps + Neon | Consumption grant: 180,000 vCPU-seconds, 360,000 GiB-seconds and 2 million requests/subscription/month | Comparable managed alternative with native scheduled jobs. Requires an Azure subscription; grants are not a hard bill cap. Account for logs, registry, storage, networking and database separately. |
| Render Free | Free web services sleep after 15 minutes; free PostgreSQL expires after 30 days | Useful preview environment, unsuitable for this complete durable app and its continuously running workers. |

Sources: [Oracle](https://docs.oracle.com/en-us/iaas/Content/FreeTier/freetier_topic-Always_Free_Resources.htm), [Cloud Run](https://cloud.google.com/run/pricing), [Neon pricing and quota behavior](https://neon.com/pricing), [Azure pricing](https://azure.microsoft.com/en-us/pricing/details/container-apps/), [Render limits](https://render.com/docs/free). The previous detailed comparison remains in [CLOUD_OPTIONS.md](CLOUD_OPTIONS.md).

Azure's standard account signup requires a phone number, a non-prepaid credit/debit card and a Microsoft or GitHub account. The initial credit is a trial, not a sustainable hosting allowance; ongoing use requires the appropriate subscription beyond that trial. [Azure account options](https://azure.microsoft.com/en-us/pricing/purchase-options/azure-account). West Europe is an EU Container Apps location, but supported regions do not guarantee capacity for a new subscription: Microsoft's Container Apps team has documented provisioning pressure there and in Germany West. Confirm actual environment/job capacity before selecting it. [Microsoft product-team capacity notice](https://github.com/microsoft/azure-container-apps/issues/1671).

## Proposed managed layout

```text
Authenticated browser -> HTTPS Cloud Run web/API -> Neon PostgreSQL (TLS)
Authenticated schedule -> finite provider job -> Oura / Suunto APIs
                                              -> encrypted durable token store
                                              -> normalized summaries + plan review
```

Serve the web assets and API under one origin. Use minimum instances zero and conservative maximum instances; this reduces exposure but does not impose a monetary cap. Choose a compatible EU application/database region, checking actual account availability and cross-provider egress before provisioning. Do not put the current unauthenticated local dashboard onto a public URL.

Replace the workers' permanent polling loops with finite executions. Oura can run hourly; Suunto stays daily/manual with the existing persistent quota reservations and idempotent upserts. An authenticated manual sync should invoke or queue a finite job rather than require an always-on queue poller. Retain a database/session lock so concurrent jobs cannot refresh the same provider token. Cloud Scheduler includes three jobs per billing account; Azure provides scheduled Container Apps jobs. [Scheduler pricing](https://cloud.google.com/scheduler/pricing), [Azure jobs](https://learn.microsoft.com/en-us/azure/container-apps/jobs).

The current writable local token JSON files cannot remain the authority on ephemeral containers. Introduce a durable encrypted token repository with atomic refresh-token replacement, worker-only access and a separate managed encryption key. Keep raw secrets and health data out of images, Git, CI artifacts and application logs. A concurrency setting alone is insufficient across independent executions.

Current stdio MCP remains local. If remote access is needed, implement an authenticated MCP transport deliberately; never expose the stdio process, database or an unauthenticated proxy publicly.

Neon Free sleeps after inactivity and has only a limited restore window; regular worker queries and connection health probes can prevent sleeping and consume its compute quota. Use bounded jobs, short-lived connections and independent encrypted backups with a tested restore. Backups themselves need storage/egress allowance accounting. Cloud spend caps can stop a service, but enforcement can lag and exclude other costs; they do not prove a zero bill. [Neon limits](https://neon.com/pricing), [Cloud spend caps](https://docs.cloud.google.com/billing/docs/how-to/budgets-spend-caps).

## Migration backlog, after selecting a host

1. Add owner login, endpoint authorization, session/CSRF protections and access tests. Keep PostgreSQL private; expose only authenticated HTTPS.
2. Add a durable encrypted provider-token repository and atomic rotation tests. Register exact HTTPS OAuth callbacks in both providers.
3. Add finite provider-job entry points and authenticated scheduling/manual invocation; preserve quota, idempotency and refresh ownership.
4. Define infrastructure and deployment for the chosen host, immutable source-reviewed images, resource limits and rollback. Build and test the host's required CPU architecture explicitly; a local Mac image is not automatically portable to every runtime. Keep deployment credentials scoped and private.
5. Test encrypted backup/restore, migration and retention using invented records; review costs for the full resource list before provisioning.
6. Deploy an isolated synthetic-data staging environment, verify authorization boundaries and failure recovery, then decide separately whether to transfer personal data.

No personal data should move to a cloud merely because the source repository or image is public. Hosting authorization and source publication remain separate decisions.
