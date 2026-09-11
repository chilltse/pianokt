#!/usr/bin/env bash
set -euo pipefail
dest="${1:?Usage: bash backend/fetch-jars.sh DIRECTORY}"
mkdir -p "$dest"
curl -fsSL --retry 2 https://repo.maven.apache.org/maven2/io/delta/delta-spark_2.13/4.0.0/delta-spark_2.13-4.0.0.jar -o "$dest/delta-spark.jar"
curl -fsSL --retry 2 https://repo.maven.apache.org/maven2/io/delta/delta-storage/4.0.0/delta-storage-4.0.0.jar -o "$dest/delta-storage.jar"
curl -fsSL --retry 2 https://repo.maven.apache.org/maven2/com/google/cloud/bigdataoss/gcs-connector/hadoop3-2.2.26/gcs-connector-hadoop3-2.2.26-shaded.jar -o "$dest/gcs.jar"
