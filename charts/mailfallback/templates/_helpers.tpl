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
