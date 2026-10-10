                         POST /bulk-actions
                                  │
                                  ▼
                          Create Bulk Action
                                  │
                                  ▼
                              Queue
                                  │
                                  ▼
                           Orchestrator
                                  │
                           Find matching IDs
                                  │
                         Create batch jobs
                                  │
                                  ▼
                              Queue
                                  │
                 ┌────────────────┼────────────────┐
                 ▼                ▼                ▼
              Worker 1         Worker 2         Worker 3
                 │                │                │
                 ▼                ▼                ▼
             DB update        DB update        DB update
                 │                │                │
                 └────────────────┼────────────────┘
                                  ▼
                          Processing Logs
                                  │
                                  ▼
                              Statistics