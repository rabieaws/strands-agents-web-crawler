"""Create S3 bucket + Bedrock Knowledge Base infrastructure for the web crawler.

This script creates:
1. S3 bucket for storing crawled documents
2. IAM role for the Knowledge Base
3. Bedrock Knowledge Base with OpenSearch Serverless vector store (quick create)
4. S3 data source connected to the Knowledge Base

Run once to set up infrastructure:
    python infra/setup_kb.py

After running, update your agent config with the output values.
"""

import json
import time
import boto3

REGION = "ca-central-1"
ACCOUNT_ID = "612673515314"
KB_NAME = "web-crawler-kb"
BUCKET_NAME = f"web-crawler-kb-docs-{ACCOUNT_ID}-{REGION}"
S3_PREFIX = "crawled-docs/"
EMBEDDING_MODEL_ARN = f"arn:aws:bedrock:{REGION}::foundation-model/amazon.titan-embed-text-v2:0"
KB_ROLE_NAME = "AmazonBedrockKBRole-web-crawler"


def create_s3_bucket(s3_client):
    """Create the S3 bucket for crawled documents."""
    print(f"Creating S3 bucket: {BUCKET_NAME}")
    try:
        s3_client.create_bucket(
            Bucket=BUCKET_NAME,
            CreateBucketConfiguration={"LocationConstraint": REGION},
        )
        print(f"  ✓ Bucket created: {BUCKET_NAME}")
    except s3_client.exceptions.BucketAlreadyOwnedByYou:
        print(f"  ✓ Bucket already exists: {BUCKET_NAME}")
    except Exception as e:
        if "BucketAlreadyExists" in str(e):
            print(f"  ✓ Bucket already exists: {BUCKET_NAME}")
        else:
            raise


def create_kb_role(iam_client):
    """Create IAM role for the Knowledge Base."""
    print(f"Creating IAM role: {KB_ROLE_NAME}")

    trust_policy = {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Effect": "Allow",
                "Principal": {"Service": "bedrock.amazonaws.com"},
                "Action": "sts:AssumeRole",
                "Condition": {
                    "StringEquals": {"aws:SourceAccount": ACCOUNT_ID},
                    "ArnLike": {
                        "aws:SourceArn": f"arn:aws:bedrock:{REGION}:{ACCOUNT_ID}:knowledge-base/*"
                    },
                },
            }
        ],
    }

    try:
        response = iam_client.create_role(
            RoleName=KB_ROLE_NAME,
            AssumeRolePolicyDocument=json.dumps(trust_policy),
            Description="Role for Bedrock Knowledge Base - Web Crawler",
        )
        role_arn = response["Role"]["Arn"]
        print(f"  ✓ Role created: {role_arn}")
    except iam_client.exceptions.EntityAlreadyExistsException:
        role_arn = f"arn:aws:iam::{ACCOUNT_ID}:role/{KB_ROLE_NAME}"
        print(f"  ✓ Role already exists: {role_arn}")

    # Attach permissions policy
    permissions_policy = {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Sid": "S3Access",
                "Effect": "Allow",
                "Action": ["s3:GetObject", "s3:ListBucket"],
                "Resource": [
                    f"arn:aws:s3:::{BUCKET_NAME}",
                    f"arn:aws:s3:::{BUCKET_NAME}/*",
                ],
            },
            {
                "Sid": "BedrockEmbeddings",
                "Effect": "Allow",
                "Action": ["bedrock:InvokeModel"],
                "Resource": [EMBEDDING_MODEL_ARN],
            },
            {
                "Sid": "OpenSearchServerless",
                "Effect": "Allow",
                "Action": ["aoss:APIAccessAll"],
                "Resource": [f"arn:aws:aoss:{REGION}:{ACCOUNT_ID}:collection/*"],
            },
        ],
    }

    iam_client.put_role_policy(
        RoleName=KB_ROLE_NAME,
        PolicyName="BedrockKBPermissions",
        PolicyDocument=json.dumps(permissions_policy),
    )
    print("  ✓ Permissions policy attached")

    # Wait for role propagation
    print("  Waiting for IAM role propagation...")
    time.sleep(10)

    return role_arn


def create_knowledge_base(bedrock_agent_client, role_arn):
    """Create the Bedrock Knowledge Base.
    
    Uses the Bedrock-managed vector store approach which auto-provisions
    OpenSearch Serverless.
    """
    print(f"Creating Knowledge Base: {KB_NAME}")

    # First check if it already exists
    try:
        response = bedrock_agent_client.list_knowledge_bases()
        for kb in response.get("knowledgeBaseSummaries", []):
            if kb["name"] == KB_NAME:
                kb_id = kb["knowledgeBaseId"]
                print(f"  ✓ Found existing Knowledge Base: {kb_id}")
                kb_details = bedrock_agent_client.get_knowledge_base(knowledgeBaseId=kb_id)
                kb_arn = kb_details["knowledgeBase"]["knowledgeBaseArn"]
                return kb_id, kb_arn
    except Exception:
        pass

    # Create with OpenSearch Serverless
    # We need to create the AOSS collection first
    aoss_client = boto3.client("opensearchserverless", region_name=REGION)
    
    collection_name = "web-crawler-kb-vectors"
    collection_arn = _create_opensearch_collection(aoss_client, collection_name, role_arn)
    
    # Create the vector index BEFORE creating the KB
    _create_vector_index(collection_arn, collection_name)
    
    try:
        response = bedrock_agent_client.create_knowledge_base(
            name=KB_NAME,
            description="Knowledge base for web-crawled content. Populated by the Strands web crawler agent.",
            roleArn=role_arn,
            knowledgeBaseConfiguration={
                "type": "VECTOR",
                "vectorKnowledgeBaseConfiguration": {
                    "embeddingModelArn": EMBEDDING_MODEL_ARN,
                    "embeddingModelConfiguration": {
                        "bedrockEmbeddingModelConfiguration": {
                            "dimensions": 1024,
                        }
                    },
                },
            },
            storageConfiguration={
                "type": "OPENSEARCH_SERVERLESS",
                "opensearchServerlessConfiguration": {
                    "collectionArn": collection_arn,
                    "vectorIndexName": "bedrock-knowledge-base-default-index",
                    "fieldMapping": {
                        "vectorField": "bedrock-knowledge-base-default-vector",
                        "textField": "AMAZON_BEDROCK_TEXT_CHUNK",
                        "metadataField": "AMAZON_BEDROCK_METADATA",
                    },
                },
            },
        )
        kb_id = response["knowledgeBase"]["knowledgeBaseId"]
        kb_arn = response["knowledgeBase"]["knowledgeBaseArn"]
        print(f"  ✓ Knowledge Base created: {kb_id}")
        
        return kb_id, kb_arn
    except Exception as e:
        raise


def _create_opensearch_collection(aoss_client, collection_name, kb_role_arn):
    """Create an OpenSearch Serverless collection for the KB."""
    print(f"  Creating OpenSearch Serverless collection: {collection_name}")
    
    # Check if collection already exists
    try:
        response = aoss_client.list_collections(
            collectionFilters={"name": collection_name}
        )
        if response.get("collectionSummaries"):
            collection = response["collectionSummaries"][0]
            arn = collection["arn"]
            print(f"  ✓ Collection already exists: {arn}")
            return arn
    except Exception:
        pass

    # Create encryption policy
    try:
        aoss_client.create_security_policy(
            name=f"{collection_name}-enc",
            type="encryption",
            policy=json.dumps({
                "Rules": [{"ResourceType": "collection", "Resource": [f"collection/{collection_name}"]}],
                "AWSOwnedKey": True,
            }),
        )
        print("  ✓ Encryption policy created")
    except Exception as e:
        if "ConflictException" in str(type(e).__name__):
            print("  ✓ Encryption policy already exists")
        else:
            raise

    # Create network policy (public access for simplicity)
    try:
        aoss_client.create_security_policy(
            name=f"{collection_name}-net",
            type="network",
            policy=json.dumps([{
                "Rules": [
                    {"ResourceType": "collection", "Resource": [f"collection/{collection_name}"]},
                    {"ResourceType": "dashboard", "Resource": [f"collection/{collection_name}"]},
                ],
                "AllowFromPublic": True,
            }]),
        )
        print("  ✓ Network policy created")
    except Exception as e:
        if "ConflictException" in str(type(e).__name__):
            print("  ✓ Network policy already exists")
        else:
            raise

    # Create data access policy
    try:
        aoss_client.create_access_policy(
            name=f"{collection_name}-access",
            type="data",
            policy=json.dumps([{
                "Rules": [
                    {
                        "ResourceType": "collection",
                        "Resource": [f"collection/{collection_name}"],
                        "Permission": ["aoss:CreateCollectionItems", "aoss:UpdateCollectionItems", "aoss:DescribeCollectionItems"],
                    },
                    {
                        "ResourceType": "index",
                        "Resource": [f"index/{collection_name}/*"],
                        "Permission": ["aoss:CreateIndex", "aoss:UpdateIndex", "aoss:DescribeIndex", "aoss:ReadDocument", "aoss:WriteDocument"],
                    },
                ],
                "Principal": [
                    kb_role_arn,
                    f"arn:aws:iam::{ACCOUNT_ID}:user/IAMAdmin",
                    f"arn:aws:iam::{ACCOUNT_ID}:role/AmazonBedrockAgentCoreSDKRuntime-ca-central-1-ae18d57598",
                ],
            }]),
        )
        print("  ✓ Data access policy created")
    except Exception as e:
        if "ConflictException" in str(type(e).__name__):
            print("  ✓ Data access policy already exists")
        else:
            raise

    # Create the collection
    try:
        response = aoss_client.create_collection(
            name=collection_name,
            type="VECTORSEARCH",
            description="Vector store for web crawler knowledge base",
        )
        collection_arn = response["createCollectionDetail"]["arn"]
        collection_id = response["createCollectionDetail"]["id"]
        print(f"  ✓ Collection creation initiated: {collection_arn}")
    except Exception as e:
        if "ConflictException" in str(type(e).__name__):
            response = aoss_client.list_collections(collectionFilters={"name": collection_name})
            collection = response["collectionSummaries"][0]
            collection_arn = collection["arn"]
            collection_id = collection["id"]
            print(f"  ✓ Collection already exists: {collection_arn}")
        else:
            raise

    # Wait for collection to become active
    print("  Waiting for collection to become ACTIVE (this may take 1-3 minutes)...")
    for i in range(60):
        time.sleep(5)
        response = aoss_client.batch_get_collection(ids=[collection_id])
        status = response["collectionDetails"][0]["status"]
        if status == "ACTIVE":
            print(f"  ✓ Collection is ACTIVE")
            break
        elif status == "FAILED":
            raise Exception(f"Collection creation failed: {response['collectionDetails'][0]}")
        if i % 6 == 0:
            print(f"    Status: {status}...")
    else:
        print("  ⚠️  Collection still creating. Proceeding anyway...")

    return collection_arn


def _create_vector_index(collection_arn, collection_name):
    """Create the vector index in the OpenSearch Serverless collection."""
    print("  Creating vector index...")
    
    from opensearchpy import OpenSearch, RequestsHttpConnection
    from requests_aws4auth import AWS4Auth
    
    # Get collection endpoint
    aoss_client = boto3.client("opensearchserverless", region_name=REGION)
    response = aoss_client.batch_get_collection(names=[collection_name])
    endpoint = response["collectionDetails"][0]["collectionEndpoint"]
    
    # Create AWS4Auth for AOSS
    session = boto3.Session()
    credentials = session.get_credentials()
    awsauth = AWS4Auth(
        credentials.access_key,
        credentials.secret_key,
        REGION,
        "aoss",
        session_token=credentials.token,
    )
    
    # Connect to OpenSearch
    client = OpenSearch(
        hosts=[{"host": endpoint.replace("https://", ""), "port": 443}],
        http_auth=awsauth,
        use_ssl=True,
        verify_certs=True,
        connection_class=RequestsHttpConnection,
        timeout=60,
    )
    
    index_name = "bedrock-knowledge-base-default-index"
    
    # Check if index exists
    if client.indices.exists(index=index_name):
        print(f"  ✓ Vector index already exists: {index_name}")
        return
    
    # Create the vector index with proper mappings for Bedrock KB
    index_body = {
        "settings": {
            "index": {
                "knn": True,
                "knn.algo_param.ef_search": 512,
            }
        },
        "mappings": {
            "properties": {
                "bedrock-knowledge-base-default-vector": {
                    "type": "knn_vector",
                    "dimension": 1024,
                    "method": {
                        "engine": "faiss",
                        "space_type": "l2",
                        "name": "hnsw",
                        "parameters": {"ef_construction": 512, "m": 16},
                    },
                },
                "AMAZON_BEDROCK_TEXT_CHUNK": {"type": "text"},
                "AMAZON_BEDROCK_METADATA": {"type": "text", "index": False},
            }
        },
    }
    
    client.indices.create(index=index_name, body=index_body)
    print(f"  ✓ Vector index created: {index_name}")


def create_data_source(bedrock_agent_client, kb_id):
    """Create S3 data source for the Knowledge Base.
    
    Note: Bedrock automatically detects .metadata.json sidecar files when they
    follow the correct format (see tools.py store_to_knowledge_base). No special
    data source configuration is needed for metadata file support.
    """
    print(f"Creating S3 data source for KB: {kb_id}")

    try:
        response = bedrock_agent_client.create_data_source(
            knowledgeBaseId=kb_id,
            name="web-crawler-docs",
            description="Crawled web documents from the Strands web crawler agent",
            dataSourceConfiguration={
                "type": "S3",
                "s3Configuration": {
                    "bucketArn": f"arn:aws:s3:::{BUCKET_NAME}",
                    "inclusionPrefixes": [S3_PREFIX],
                },
            },
        )
        ds_id = response["dataSource"]["dataSourceId"]
        print(f"  ✓ Data source created: {ds_id}")
        return ds_id
    except Exception as e:
        if "ConflictException" in str(type(e).__name__) or "already exists" in str(e).lower():
            # Find existing data source
            response = bedrock_agent_client.list_data_sources(knowledgeBaseId=kb_id)
            for ds in response.get("dataSourceSummaries", []):
                if ds["name"] == "web-crawler-docs":
                    ds_id = ds["dataSourceId"]
                    print(f"  ✓ Found existing data source: {ds_id}")
                    return ds_id
        raise


def main():
    print("=" * 60)
    print("Setting up S3 + Bedrock Knowledge Base Infrastructure")
    print(f"Region: {REGION}")
    print("=" * 60)
    print()

    s3_client = boto3.client("s3", region_name=REGION)
    iam_client = boto3.client("iam")
    bedrock_agent_client = boto3.client("bedrock-agent", region_name=REGION)

    # Step 1: Create S3 bucket
    create_s3_bucket(s3_client)
    print()

    # Step 2: Create IAM role
    role_arn = create_kb_role(iam_client)
    print()

    # Step 3: Create Knowledge Base
    kb_id, kb_arn = create_knowledge_base(bedrock_agent_client, role_arn)
    print()

    # Step 4: Create Data Source
    ds_id = create_data_source(bedrock_agent_client, kb_id)
    print()

    # Output configuration
    print("=" * 60)
    print("✅ SETUP COMPLETE")
    print("=" * 60)
    print()
    print("Add these to your agent configuration:")
    print(f"  S3_BUCKET={BUCKET_NAME}")
    print(f"  S3_PREFIX={S3_PREFIX}")
    print(f"  KB_ID={kb_id}")
    print(f"  KB_DATA_SOURCE_ID={ds_id}")
    print()
    print("Knowledge Base ARN:")
    print(f"  {kb_arn}")
    print()
    print("To sync the KB after crawling:")
    print(f"  aws bedrock-agent start-ingestion-job --knowledge-base-id {kb_id} --data-source-id {ds_id} --region {REGION}")


if __name__ == "__main__":
    main()
