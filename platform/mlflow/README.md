# MLflow platform boundary

MLflow tracks synthetic evaluation runs and model metadata. Configure `MLFLOW_TRACKING_URI` outside version control. Log aggregate metrics, immutable model identifiers, contract versions, and safe artifact references only; do not log payloads or secrets.
