{{/*
Expand the name of the chart.
*/}}
{{- define "vafi.name" -}}
{{- default .Chart.Name .Values.nameOverride | trunc 63 | trimSuffix "-" }}
{{- end }}

{{/*
Create a default fully qualified app name.
*/}}
{{- define "vafi.fullname" -}}
{{- if .Values.fullnameOverride }}
{{- .Values.fullnameOverride | trunc 63 | trimSuffix "-" }}
{{- else }}
{{- $name := default .Chart.Name .Values.nameOverride }}
{{- if contains $name .Release.Name }}
{{- .Release.Name | trunc 63 | trimSuffix "-" }}
{{- else }}
{{- printf "%s-%s" .Release.Name $name | trunc 63 | trimSuffix "-" }}
{{- end }}
{{- end }}
{{- end }}

{{/*
Common labels applied to every resource.
*/}}
{{- define "vafi.labels" -}}
helm.sh/chart: {{ include "vafi.name" . }}-{{ .Chart.Version | replace "+" "_" }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
app.kubernetes.io/part-of: vafi
{{- end }}

{{/*
Selector labels for a given component.
IMPORTANT: Does NOT include version — selectors must be immutable.
Usage: {{ include "vafi.selectorLabels" (dict "root" . "component" "executor") }}
*/}}
{{- define "vafi.selectorLabels" -}}
app.kubernetes.io/name: {{ include "vafi.name" .root }}
app.kubernetes.io/instance: {{ .root.Release.Name }}
app.kubernetes.io/component: {{ .component }}
{{- end }}

{{/*
Secret name used by all components.
Uses existingSecret if set, otherwise generates from release name.
*/}}
{{- define "vafi.secretName" -}}
{{- if .Values.secrets.existingSecret -}}
{{- .Values.secrets.existingSecret -}}
{{- else -}}
{{ include "vafi.fullname" . }}-secrets
{{- end -}}
{{- end }}

{{/*
CXDB service name.
*/}}
{{- define "vafi.cxdbName" -}}
{{ include "vafi.fullname" . }}-cxdb
{{- end }}

{{/*
SSH secret name for the judge pod (vafi#37 capability slice). Defaults to
the shared sshSecret.name so existing deployments are unaffected. When
judgeSshSecret.name is set, the judge mounts that secret instead — the
operator's hook for landing a read-only deploy key on the judge pod
without touching the executor's credentials.
*/}}
{{- define "vafi.judgeSshSecretName" -}}
{{- if .Values.judgeSshSecret.name -}}
{{- .Values.judgeSshSecret.name -}}
{{- else -}}
{{- .Values.sshSecret.name -}}
{{- end -}}
{{- end }}

{{/*
Bridge agent-pi image string. Accepts either the legacy single-string form
("repo:tag") or the preferred {repository, tag} map form. The map form is
required for build pipelines that bump tags in isolation (e.g. git-tag-bump
in the vafi build Workflow); the legacy form remains supported so existing
values files keep working.
*/}}
{{- define "vafi.bridgeAgentPiImage" -}}
{{- $img := .Values.bridge.agentPiImage -}}
{{- if kindIs "map" $img -}}
{{- printf "%s:%s" $img.repository $img.tag -}}
{{- else -}}
{{- $img -}}
{{- end -}}
{{- end }}

{{/*
Common environment variables for the executor container.
*/}}
{{- define "vafi.executorEnv" -}}
{{- if .Values.executor.agentId }}
- name: VF_AGENT_ID
  value: {{ .Values.executor.agentId | quote }}
{{- end }}
- name: VF_AGENT_ROLE
  value: {{ .Values.executor.role | quote }}
- name: VF_AGENT_TAGS
  value: {{ .Values.executor.tags | quote }}
- name: VF_VTF_API_URL
  value: {{ .Values.vtf.apiUrl | quote }}
- name: VF_VTF_TOKEN
  valueFrom:
    secretKeyRef:
      name: {{ include "vafi.secretName" . }}
      key: vtf-token
- name: VF_POLL_INTERVAL
  value: {{ .Values.executor.pollInterval | quote }}
- name: VF_TASK_TIMEOUT
  value: {{ .Values.executor.taskTimeout | quote }}
- name: VF_MAX_REWORK
  value: {{ .Values.executor.maxRework | quote }}
- name: VF_MAX_TURNS
  value: {{ .Values.executor.maxTurns | quote }}
- name: VF_HEARTBEAT_INTERVAL
  value: {{ .Values.executor.heartbeatInterval | quote }}
- name: VF_SESSIONS_DIR
  value: {{ .Values.executor.sessionsDir | quote }}
- name: VF_VAULT_ADDR
  value: {{ .Values.vault.addr | quote }}
- name: VF_VAULT_SKIP_VERIFY
  value: {{ .Values.vault.skipVerify | quote }}
- name: VF_CONTROLLER_ENV
  value: {{ .Values.vault.controllerEnv | quote }}
{{- if .Values.cxdb.enabled }}
- name: VF_CXDB_URL
  value: "http://{{ include "vafi.cxdbName" . }}:80"
{{- if .Values.cxdb.publicUrl }}
- name: VF_CXDB_PUBLIC_URL
  value: {{ .Values.cxdb.publicUrl | quote }}
{{- end }}
{{- end }}
- name: ANTHROPIC_AUTH_TOKEN
  valueFrom:
    secretKeyRef:
      name: {{ include "vafi.secretName" . }}
      key: anthropic-auth-token
- name: ANTHROPIC_BASE_URL
  valueFrom:
    secretKeyRef:
      name: {{ include "vafi.secretName" . }}
      key: anthropic-base-url
{{- if .Values.vfobs.emitEnabled }}
- name: VFOBS_EMIT_ENABLED
  value: "true"
- name: VFOBS_EMIT_URL
  value: {{ .Values.vfobs.emitUrl | quote }}
- name: VFOBS_EMIT_TOKEN
  valueFrom:
    secretKeyRef:
      name: {{ include "vafi.secretName" . }}
      key: vfobs-ingest-token
{{- end }}
{{- end }}

{{/*
Environment variables for the pi-harness executor container.
Mirrors vafi.executorEnv but reads from .Values.executorPi and adds the
harness-specific vars (VF_HARNESS, VF_PI_PROVIDER, VF_PI_MODEL).
The pi binary reads ANTHROPIC_API_KEY (anthropic SDK), not ANTHROPIC_AUTH_TOKEN.
*/}}
{{- define "vafi.executorPiEnv" -}}
{{- if .Values.executorPi.agentId }}
- name: VF_AGENT_ID
  value: {{ .Values.executorPi.agentId | quote }}
{{- end }}
- name: VF_AGENT_ROLE
  value: {{ .Values.executorPi.role | quote }}
- name: VF_AGENT_TAGS
  value: {{ .Values.executorPi.tags | quote }}
- name: VF_HARNESS
  value: {{ .Values.executorPi.harness | quote }}
- name: VF_PI_PROVIDER
  value: {{ .Values.executorPi.piProvider | quote }}
- name: VF_PI_MODEL
  value: {{ .Values.executorPi.piModel | quote }}
- name: VF_VTF_API_URL
  value: {{ .Values.vtf.apiUrl | quote }}
- name: VF_VTF_TOKEN
  valueFrom:
    secretKeyRef:
      name: {{ include "vafi.secretName" . }}
      key: vtf-token
- name: VF_POLL_INTERVAL
  value: {{ .Values.executorPi.pollInterval | quote }}
- name: VF_TASK_TIMEOUT
  value: {{ .Values.executorPi.taskTimeout | quote }}
- name: VF_MAX_REWORK
  value: {{ .Values.executorPi.maxRework | quote }}
- name: VF_MAX_TURNS
  value: {{ .Values.executorPi.maxTurns | quote }}
- name: VF_HEARTBEAT_INTERVAL
  value: {{ .Values.executorPi.heartbeatInterval | quote }}
- name: VF_SESSIONS_DIR
  value: {{ .Values.executorPi.sessionsDir | quote }}
- name: VF_VAULT_ADDR
  value: {{ .Values.vault.addr | quote }}
- name: VF_VAULT_SKIP_VERIFY
  value: {{ .Values.vault.skipVerify | quote }}
- name: VF_CONTROLLER_ENV
  value: {{ .Values.vault.controllerEnv | quote }}
{{- if .Values.cxdb.enabled }}
- name: VF_CXDB_URL
  value: "http://{{ include "vafi.cxdbName" . }}:80"
{{- if .Values.cxdb.publicUrl }}
- name: VF_CXDB_PUBLIC_URL
  value: {{ .Values.cxdb.publicUrl | quote }}
{{- end }}
{{- end }}
- name: ANTHROPIC_API_KEY
  valueFrom:
    secretKeyRef:
      name: {{ include "vafi.secretName" . }}
      key: anthropic-auth-token
- name: ANTHROPIC_BASE_URL
  valueFrom:
    secretKeyRef:
      name: {{ include "vafi.secretName" . }}
      key: anthropic-base-url
{{- if .Values.vfobs.emitEnabled }}
- name: VFOBS_EMIT_ENABLED
  value: "true"
- name: VFOBS_EMIT_URL
  value: {{ .Values.vfobs.emitUrl | quote }}
- name: VFOBS_EMIT_TOKEN
  valueFrom:
    secretKeyRef:
      name: {{ include "vafi.secretName" . }}
      key: vfobs-ingest-token
{{- end }}
{{- end }}

{{/*
Environment variables for the judge container.
Same structure as executor but reads from .Values.judge.
*/}}
{{- define "vafi.judgeEnv" -}}
{{- if .Values.judge.agentId }}
- name: VF_AGENT_ID
  value: {{ .Values.judge.agentId | quote }}
{{- end }}
- name: VF_AGENT_ROLE
  value: {{ .Values.judge.role | quote }}
- name: VF_AGENT_TAGS
  value: {{ .Values.judge.tags | quote }}
- name: VF_VTF_API_URL
  value: {{ .Values.vtf.apiUrl | quote }}
- name: VF_VTF_TOKEN
  valueFrom:
    secretKeyRef:
      name: {{ include "vafi.secretName" . }}
      key: vtf-token
- name: VF_POLL_INTERVAL
  value: {{ .Values.judge.pollInterval | quote }}
- name: VF_TASK_TIMEOUT
  value: {{ .Values.judge.taskTimeout | quote }}
- name: VF_MAX_TURNS
  value: {{ .Values.judge.maxTurns | quote }}
- name: VF_HEARTBEAT_INTERVAL
  value: {{ .Values.judge.heartbeatInterval | quote }}
- name: VF_SESSIONS_DIR
  value: {{ .Values.judge.sessionsDir | quote }}
{{- if .Values.cxdb.enabled }}
- name: VF_CXDB_URL
  value: "http://{{ include "vafi.cxdbName" . }}:80"
{{- if .Values.cxdb.publicUrl }}
- name: VF_CXDB_PUBLIC_URL
  value: {{ .Values.cxdb.publicUrl | quote }}
{{- end }}
{{- end }}
- name: ANTHROPIC_AUTH_TOKEN
  valueFrom:
    secretKeyRef:
      name: {{ include "vafi.secretName" . }}
      key: anthropic-auth-token
- name: ANTHROPIC_BASE_URL
  valueFrom:
    secretKeyRef:
      name: {{ include "vafi.secretName" . }}
      key: anthropic-base-url
{{- if .Values.vfobs.emitEnabled }}
- name: VFOBS_EMIT_ENABLED
  value: "true"
- name: VFOBS_EMIT_URL
  value: {{ .Values.vfobs.emitUrl | quote }}
- name: VFOBS_EMIT_TOKEN
  valueFrom:
    secretKeyRef:
      name: {{ include "vafi.secretName" . }}
      key: vfobs-ingest-token
{{- end }}
{{- end }}
