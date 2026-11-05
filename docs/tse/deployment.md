export PROJECT_ID="pianokt"
export REGION="us-central1"

export VERSION="0.12.0"
export SHA=$(git rev-parse HEAD)

export IMAGE_BASE="${REGION}-docker.pkg.dev/${PROJECT_ID}/pianokt/backend"
export IMAGE="${IMAGE_BASE}:${VERSION}-${SHA}"

echo "$IMAGE"





git tag -a "v${VERSION}" -m "PianoKT ${VERSION}"
git push origin "v${VERSION}"




gcloud builds submit ./backend \
  --project="$PROJECT_ID" \
  --region="$REGION" \
  --tag="$IMAGE"




  gcloud run services update pianokt-worker \
  --image="$IMAGE" \
  --region="$REGION" \
  --project="$PROJECT_ID"



  gcloud run jobs update pianokt-relay \
  --image="$IMAGE" \
  --region="$REGION" \
  --project="$PROJECT_ID"





gcloud run deploy pianokt-api \
  --image="$IMAGE" \
  --region="$REGION" \
  --project="$PROJECT_ID"




  修改：

infra/gcp/terraform.tfvars
image = "us-central1-docker.pkg.dev/pianokt/pianokt/backend:0.10.0-完整SHA"


还要检查 Pub/Sub 对 performance.submitted 的订阅过滤器、Relay 的一分钟调度及暂停设置。



梯子：
export HTTP_PROXY=http://127.0.0.1:10808
export HTTPS_PROXY=http://127.0.0.1:10808

export http_proxy=$HTTP_PROXY
export https_proxy=$HTTPS_PROXY

随后执行：
terraform -chdir=infra/gcp plan