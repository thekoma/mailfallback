{{/* In-cluster service names (release-scoped; the app service is force-named). */}}
{{- define "mailfallback.appServiceName" -}}
mailfallback
{{- end -}}
{{- define "mailfallback.dovecotServiceName" -}}
{{ .Release.Name }}-dovecot
{{- end -}}
{{- define "mailfallback.tikaServiceName" -}}
{{ .Release.Name }}-tika
{{- end -}}
{{- define "mailfallback.isRwx" -}}
{{- if has "ReadWriteMany" .Values.storage.maildirs.accessModes }}true{{ else }}false{{ end -}}
{{- end -}}
{{/* Secret names for envFrom: chart-managed when inline is enabled, else the user-provided existing Secret. */}}
{{- define "mailfallback.appSecretName" -}}
{{- if .Values.inlineSecrets.app.enabled }}{{ .Release.Name }}-app-env{{ else }}{{ .Values.existingSecrets.app }}{{ end -}}
{{- end -}}
{{- define "mailfallback.roundcubeSecretName" -}}
{{- if .Values.inlineSecrets.roundcube.enabled }}{{ .Release.Name }}-roundcube-env{{ else }}{{ .Values.existingSecrets.roundcube }}{{ end -}}
{{- end -}}
