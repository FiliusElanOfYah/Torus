#====================================================================================================
# START - Testing Protocol - DO NOT EDIT OR REMOVE THIS SECTION
#====================================================================================================

# THIS SECTION CONTAINS CRITICAL TESTING INSTRUCTIONS FOR BOTH AGENTS
# BOTH MAIN_AGENT AND TESTING_AGENT MUST PRESERVE THIS ENTIRE BLOCK

# Communication Protocol:
# If the `testing_agent` is available, main agent should delegate all testing tasks to it.
#
# You have access to a file called `test_result.md`. This file contains the complete testing state
# and history, and is the primary means of communication between main and the testing agent.
#
# Main and testing agents must follow this exact format to maintain testing data. 
# The testing data must be entered in yaml format Below is the data structure:
# 
## user_problem_statement: {problem_statement}
## backend:
##   - task: "Task name"
##     implemented: true
##     working: true  # or false or "NA"
##     file: "file_path.py"
##     stuck_count: 0
##     priority: "high"  # or "medium" or "low"
##     needs_retesting: false
##     status_history:
##         -working: true  # or false or "NA"
##         -agent: "main"  # or "testing" or "user"
##         -comment: "Detailed comment about status"
##
## frontend:
##   - task: "Task name"
##     implemented: true
##     working: true  # or false or "NA"
##     file: "file_path.js"
##     stuck_count: 0
##     priority: "high"  # or "medium" or "low"
##     needs_retesting: false
##     status_history:
##         -working: true  # or false or "NA"
##         -agent: "main"  # or "testing" or "user"
##         -comment: "Detailed comment about status"
##
## metadata:
##   created_by: "main_agent"
##   version: "1.0"
##   test_sequence: 0
##   run_ui: false
##
## test_plan:
##   current_focus:
##     - "Task name 1"
##     - "Task name 2"
##   stuck_tasks:
##     - "Task name with persistent issues"
##   test_all: false
##   test_priority: "high_first"  # or "sequential" or "stuck_first"
##
## agent_communication:
##     -agent: "main"  # or "testing" or "user"
##     -message: "Communication message between agents"

# Protocol Guidelines for Main agent
#
# 1. Update Test Result File Before Testing:
#    - Main agent must always update the `test_result.md` file before calling the testing agent
#    - Add implementation details to the status_history
#    - Set `needs_retesting` to true for tasks that need testing
#    - Update the `test_plan` section to guide testing priorities
#    - Add a message to `agent_communication` explaining what you've done
#
# 2. Incorporate User Feedback:
#    - When a user provides feedback that something is or isn't working, add this information to the relevant task's status_history
#    - Update the working status based on user feedback
#    - If a user reports an issue with a task that was marked as working, increment the stuck_count
#    - Whenever user reports issue in the app, if we have testing agent and task_result.md file so find the appropriate task for that and append in status_history of that task to contain the user concern and problem as well 
#
# 3. Track Stuck Tasks:
#    - Monitor which tasks have high stuck_count values or where you are fixing same issue again and again, analyze that when you read task_result.md
#    - For persistent issues, use websearch tool to find solutions
#    - Pay special attention to tasks in the stuck_tasks list
#    - When you fix an issue with a stuck task, don't reset the stuck_count until the testing agent confirms it's working
#
# 4. Provide Context to Testing Agent:
#    - When calling the testing agent, provide clear instructions about:
#      - Which tasks need testing (reference the test_plan)
#      - Any authentication details or configuration needed
#      - Specific test scenarios to focus on
#      - Any known issues or edge cases to verify
#
# 5. Call the testing agent with specific instructions referring to test_result.md
#
# IMPORTANT: Main agent must ALWAYS update test_result.md BEFORE calling the testing agent, as it relies on this file to understand what to test next.

#====================================================================================================
# END - Testing Protocol - DO NOT EDIT OR REMOVE THIS SECTION
#====================================================================================================



#====================================================================================================
# Testing Data - Main Agent and testing sub agent both should log testing data below this section
#====================================================================================================
# 2026-10-24 main agent: Phase 8 (hardening) + Phase 9 (export/restore) in torus/.
# Local mesh: torus/tests/run_local_mesh.sh start (TORUS_HOME=/tmp/torus-test, venv /tmp/tv, nats-server in /usr/local/bin).
# fake Claude: /tmp/tv/bin/python torus/tests/fake_llm.py (replaces llmd).
# Own tests: /tmp/tv/bin/python -m pytest -p no:cacheprovider -c /dev/null backend/tests/test_torus_phase8_9.py  -> 16/16.
# Gateway: http://127.0.0.1:7777 (needs X-Torus: 1 header; /api/export POST + /api/code need X-Torus-Token from /tmp/torus-test/etc/gateway.token).
# Settings tab shows Security audit + Backup cards (data-testid settings-audit, audit-run, audit-item-<id>, settings-export, export-run, export-with-keys).
# 2026-10-26 main agent: Phase 7 oracled (pinned-topic refill) + Phase 7/8/9 catalog rewrite in backend/phases.py.
# Local mesh running: TORUS_HOME=/tmp/torus-test, gateway http://127.0.0.1:7777 (X-Torus: 1 header). oracled is in the mesh (torus/tests/run_local_mesh.sh).
# New API (no token needed): GET /api/oracle (status), POST /api/oracle/run {topic?}, GET/POST /api/oracle/config {yaml}.
# Vault tab card (data-testid vault-oracle, oracle-summary, oracle-topic-<name>, oracle-run, oracle-run-topic, oracle-edit, oracle-yaml, oracle-save, oracle-cancel).
# Own tests: /tmp/tv/bin/python -m pytest -n 0 -p no:cacheprovider backend/tests/test_torus_oracle.py -> 15/15; phase8_9 + iter7 suites 31/31 still green.
# backend/.env + frontend/.env were missing after the fork; recreated (local Mongo). Web-app backend on :8001 serves /api/phases/{7,8,9} with the new catalog.
