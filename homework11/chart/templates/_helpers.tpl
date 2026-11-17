{{- define "inference.name" -}}
{{ .Release.Name }}-inference
{{- end -}}
{{- define "inference.labels" -}}
app.kubernetes.io/name: inference
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end -}}
