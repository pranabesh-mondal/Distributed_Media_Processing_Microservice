# Distributed_Media_Processing_Microservice


## Table of Contents

- [Executive Summary](#executive-summary)
- [Objectives](#objectives)
- [Technical Architecture](#technical-architecture)
- [Media Operations (Week 3)](#media-operations-week-3)
- [Workflow](#workflow)
- [Development Timeline](#development-timeline)
- [Technical Challenges](#technical-challenges)
- [Expected Impact](#expected-impact)
- [Deliverables](#deliverables)
- [Risk Assessment & Mitigation](#risk-assessment--mitigation)
- [Conclusion](#conclusion)
- [Prerequisites](#prerequisites)
- [Installation](#installation)
- [Environment Variables](#environment-variables)
- [Project Structure (actual, runtime)](#project-structure-actual-runtime)
- [Job Lifecycle](#job-lifecycle)
- [Running the Project](#running-the-project)
- [API Documentation](#api-documentation)
- [Testing](#testing)
- [Contributing](#contributing)
- [License](#license)


## Executive Summary

This project delivers a scalable, event-driven backend microservice that offloads CPU-intensive media processing tasks from the main web application. By leveraging FastAPI, Celery, RabbitMQ, Redis, and AWS Cloud infrastructure, it ensures high responsiveness, elasticity, and fault tolerance. The system processes user-uploaded media (images/videos), optimizes them, and serves them globally via a CDN, dramatically improving user experience.


## Objectives

- Performance Optimization: Offload heavy media tasks from the main app.
- Scalability: Auto-scale worker nodes during traffic spikes.
- Reliability: Ensure fault tolerance with retries and error handling.
- Global Delivery: Store and serve optimized assets via AWS S3 + CloudFront.
- Monitoring: Provide real-time visibility into system health and workload.


## Technical Architecture

| Layer           | Technology          | Function                                                                 |
|-----------------|---------------------|--------------------------------------------------------------------------|
| API Layer       | FastAPI             | REST endpoints, job submission, pre-signed S3 URLs                       |
| Task Queue      | Celery              | Asynchronous job execution                                               |
| Message Broker  | RabbitMQ            | Distributes tasks to workers                                             |
| Cache/Status    | Redis               | Tracks job states (pending, processing, completed, failed)               |
| Media Process   | Pillow, FFmpeg      | Image resizing, compression, watermarking; video transcoding, thumbnails |
| Storage & CDN   | AWS S3 + CloudFront | Stores processed assets, delivers globally                               |
| Monitoring      | Prometheus          | Queue length, worker CPU/memory usage                                    |

## Media Operations (Week 3)

Jobs carry an ordered list of operations executed by the worker pipeline.

### Image (Pillow — `app/services/processing/image_processor.py`)

| Operation    | Parameters    |
|--------------|--------------------------------------------------------------------------------------------------------------|
| `resize`     | `width`, `height` (at least one; single dimension preserves aspect ratio), `allow_upscale` (default`false`)  |
| `compress`   | `format` (`jpeg`/`png`/`webp`/`bmp`/`gif`), `quality` (1–95, default `85`)                                   |
| `watermark`  | `text` (required), `opacity` (0–100), `position` (`top-left`…`bottom-right`/`center`), `font_size`, `margin` |
| `thumbnail`  | `width`, `height` (default `320`x`240`); fits within the box, aspect preserved                               |

### Video (FFmpeg — `app/services/processing/video_processor.py`)

Requires the **FFmpeg system binary** on PATH; a missing binary raises a clear
`FFmpegNotAvailableError` (the job fails permanently, it is not retried).

| Operation    | Parameters                                                                                               |
|--------------|----------------------------------------------------------------------------------------------------------|
| `transcode`  | `format` (`mp4`/`webm`/`avi`/`mov`), `crf` (0–51), `preset`, `video_bitrate`                             |
| `compress`   | `crf`, `preset`, `video_bitrate`                                                                         |
| `resize`     | `width`, `height` (at least one; the other is scaled to keep the aspect ratio)                           |
| `thumbnail`  | `timestamp` (seconds, default `1.0`), `width`, `height` — extracted as JPEG and uploaded to S3           |

### Example: submit a job with operations

```bash
curl -X POST http://localhost:8000/api/v1/jobs \
  -H "Content-Type: application/json" \
  -d '{
    "media_type": "image",
    "source_key": "uploads/images/921827...jpg",
    "operations": [
      {"op": "resize",   "params": {"width": 1280}},
      {"op": "watermark","params": {"text": "© MySite", "opacity": 60}},
      {"op": "compress", "params": {"format": "webp", "quality": 75}}
    ]
  }'
```

`GET /api/v1/jobs/{job_id}` returns the status, the requested operations and,
once completed, the `result_key`, `thumbnail_keys`, CDN `result_url` / 
`thumbnail_urls` and processing `metadata` (format, dimensions, duration, size).


## Workflow

**Upload Initiation**
- Client requests job -> FastAPI generates pre-signed S3 URL.
- Client uploads directly to S3.

**Job Submission**
- FastAPI registers job in Redis (status: pending).
- Job pushed into RabbitMQ.

**Worker Execution**
- Celery workers pull jobs.
- Workers download file from S3, process it (resize, compress, transcode).
- Processed file re-uploaded to S3.

**Completion**
- Redis status updated to "completed" or "failed".
- Client polls job status or receives callback.


## Development Timeline

**Week 1: API & Storage** — Complete
- FastAPI scaffolding.
- Boto3 integration for S3 access.
- Pre-signed URL logic.
- Redis setup for job tracking.

**Week 2: Queue & Workers** — Complete
- RabbitMQ deployment (docker-compose).
- Celery integration with retry policies.
- Worker simulation.
- Error handling for network failures.

**Week 3: Media Logic** — Complete
- Pillow scripts for image transformations.
- FFmpeg integration for video transcoding.
- Thumbnail extraction.
- End-to-end local testing.

**Week 4: Infrastructure & Monitoring** — Complete
- Prometheus metrics integration (`GET /metrics`).
- CloudFront CDN delivery (`result_url` / `thumbnail_urls`) + pre-signed download URLs.
- Dockerized API + worker (`Dockerfile`, full `docker-compose.yml`).
- Test suite (`pytest`) with coverage reporting.
- Load/testing script (`scripts/load_test.py`) with concurrent uploads.


## Technical Challenges

- Concurrency: Handling thousands of uploads simultaneously.
- Fault Tolerance: Retry logic without overloading queues.
- Media Complexity: Large video files consuming CPU/memory.
- Scalability: Auto-scaling Celery workers.
- Monitoring: Real-time visibility into system health.


## Expected Impact

- Performance Boost: Main app remains responsive.
- Scalability: Elastic worker pool handles traffic surges.
- User Experience: Faster uploads, optimized delivery via CDN.
- Reliability: Robust error handling prevents job loss.


## Deliverables

- FastAPI-based microservice.
- Celery worker pool with RabbitMQ integration.
- Redis job tracking system.
- Media processing scripts (Pillow + FFmpeg).
- AWS S3 + CloudFront integration.
- Prometheus monitoring dashboard.
- Documentation + test suite.


## Risk Assessment & Mitigation

- **Risk:** Worker overload during traffic spikes.
- **Mitigation:** Auto-scaling with Kubernetes or AWS ECS.

- **Risk:** Large video files causing memory leaks.
- **Mitigation:** Stream-based processing, memory profiling.


## Conclusion

This microservice architecture provides a robust foundation for handling media processing at scale. By decoupling the processing workload from the main application, the system achieves better performance, scalability, and maintainability. The combination of modern technologies FastAPI, Celery, RabbitMQ, Redis, and AWS services, ensures a production-ready solution that can handle high-volume media processing demands while maintaining reliability and observability.


## Prerequisites

Before running this project, ensure you have the following installed:

- **Python** 3.9+
- **FFmpeg** (system binary for video processing; Python wrapper `ffmpeg-python` available)
- **RabbitMQ** (message broker)
- **Redis** (cache/status tracker)
- **AWS Account** with S3 and CloudFront configured
- **Git** (for version control)


## Installation

### Clone the Repository

```bash
git clone https://github.com/pranabesh-mondal/Distributed_Media_Processing_Microservice.git
cd Distributed_Media_Processing_Microservice
```

### Set Up Virtual Environment

```bash
# Create virtual environment
python -m venv venv

# Activate on Windows
venv\Scripts\activate

# Activate on macOS/Linux
source venv/bin/activate
```

### Install Dependencies

```bash
pip install -r requirements.txt
```


## Environment Variables

Copy `.env.example` to `.env` and fill in your values (never commit `.env`).
Key variables:

| Variable                                      | Purpose                                              |
|-----------------------------------------------|------------------------------------------------------|
| `AWS_ACCESS_KEY_ID` / `AWS_SECRET_ACCESS_KEY` | AWS credentials (empty → standard credential chain)  |
| `AWS_REGION`, `S3_BUCKET_NAME`                | S3 target bucket and region                          |
| `CLOUDFRONT_DOMAIN`                           | CDN domain for serving processed assets              |
| `S3_PRESIGNED_URL_EXPIRY`                     | Upload/download URL validity in seconds (default `3600`) |
| `REDIS_HOST`, `REDIS_PORT`, `REDIS_DB`        | Redis job tracking                                   |
| `REDIS_JOB_TTL`                               | Job record TTL in Redis (default `86400`)            |
| `CELERY_BROKER_URL`                           | RabbitMQ broker URL                                  |
| `CELERY_RESULT_BACKEND`                       | Celery result backend (Redis)                        |
| `CELERY_QUEUE`                                | Queue workers consume from (default `media`)         |
| `WORKER_MAX_RETRIES`, `RETRY_BACKOFF`, ...    | Retry / network-failure handling                     |

See `.env.example` for the full annotated list.


## Project Structure (actual, runtime)

```
Distributed_Media_Processing_Microservice/
|-- app/
│   |-- __init__.py
│   |-- main.py                          # FastAPI application entry point
│   |-- config.py                        # Typed settings (pydantic-settings)
│   |-- models/                          # Pydantic request/response schemas
│   │   |-- __init__.py
│   │   |-- job.py                       # Job, JobStatus, JobCreateRequest
│   │   |-- media.py                     # MediaType, MediaOperation, upload URL schemas
│   |-- routers/                         # API endpoints
│   │   |-- __init__.py
│   │   |-- health.py                    # GET /health
│   │   |-- upload.py                    # GET /api/v1/upload-url (pre-signed S3 URL)
│   │   |-- job.py                       # POST /api/v1/jobs, GET /api/v1/jobs/{job_id}
│   |-- services/                        # Infrastructure & business logic
│   │   |-- __init__.py
│   │   |-- s3_service.py                # Boto3 S3 client (pre-signed URLs, upload/download)
│   │   |-- redis_service.py             # Job status/payload/result tracking (with retries)
│   │   |-- celery_service.py            # Celery app config, resilient dispatch
│   │   |-- retries.py                   # Transient vs permanent error taxonomy + backoff
│   │   |-- metrics.py                   # Prometheus metric registry
│   │   |-- processing/                  # Media processing scripts
│   │       |-- __init__.py              # Shared helpers, format map
│   │       |-- image_processor.py       # Pillow: resize, compress, watermark, thumbnail
│   │       |-- video_processor.py       # FFmpeg: transcode, compress, resize, thumbnails
│   |-- utils/
│   │   |-- __init__.py
│   │   |-- helpers.py                   # Object-key generation, MIME inference, CDN URLs
│   |-- workers/
│       |-- __init__.py
│       |-- celery_worker.py             # Celery tasks: full media pipeline
|-- tests/                               # pytest suite (hermetic)
│   |-- test_helpers.py
│   |-- test_retries.py
│   |-- test_image_processor.py
│   |-- test_metrics.py
│   |-- test_models.py
|-- scripts/
│   |-- load_test.py                     # Concurrent upload + job load tester
|-- monitoring/
│   |-- prometheus.yml                   # Prometheus scrape config
|-- Dockerfile
|-- pytest.ini
|-- docker-compose.yml                   # Redis + RabbitMQ + API + worker + Prometheus
|-- requirements.txt                     # Python dependencies
|-- .env.example                         # Example environment variables
|-- .gitignore                           # Git ignore file
|-- README.md                            # This file
```


## API Endpoints (implemented)

| Method | Endpoint              | Description                                        |
|--------|-----------------------|----------------------------------------------------|
| POST   | /api/v1/jobs          | Create a media processing job (with operations)    |
| GET    | /api/v1/jobs/{job_id} | Job status, operations, result keys and metadata   |
| GET    | /api/v1/jobs/{job_id}/download-url | Pre-signed S3 download URL for the result |
| GET    | /api/v1/upload-url    | Generate pre-signed S3 upload URL                  |
| GET    | /health               | Health check endpoint                              |
| GET    | /metrics              | Prometheus metrics (Prometheus text format)        |
| GET    | /docs, /redoc         | Interactive API documentation                      |


## Job Lifecycle

1. **Upload** - client calls `GET /api/v1/upload-url` and PUTs the file directly to S3.
2. **Submit** - client calls `POST /api/v1/jobs` with the `source_key` and the
   ordered `operations` list; the job is registered as `pending` in Redis and
   dispatched to Celery/RabbitMQ.
3. **Process** - a worker picks the job up (`processing`), downloads the source
   from S3 to a temp file, runs the operations (Pillow/FFmpeg), uploads the
   result (and video thumbnails) back to S3 under `processed/…`.
4. **Poll** - `GET /api/v1/jobs/{job_id}` returns `completed` with the
   `result_key`, `thumbnail_keys` and processing `metadata`, or `failed` with a
   stored `error` message. Transient network failures are retried automatically
   (exponential backoff); permanent failures (corrupt media, invalid ops) fail
   immediately.


## Running the Project

### Running Locally (Development)

#### 1. Start Infrastructure Services

Using Docker (recommended):

```bash
docker compose up -d
```

This starts **everything**: Redis (localhost:6379), RabbitMQ (localhost:5672,
management UI at http://localhost:15672), the API (localhost:8000), a Celery
worker, and Prometheus (localhost:9090). The API and worker images are built
from the bundled `Dockerfile`.

If you only need the infrastructure (to run the API/worker natively), start
just the backing services:

```bash
docker compose up -d redis rabbitmq
```

Or run them natively:

```bash
# Start Redis
redis-server

# Start RabbitMQ
rabbitmq-server
```

#### 2. Start Celery Worker

```bash
# In a new terminal, activate virtual environment
venv\Scripts\activate

# Start Celery worker
celery -A app.workers.celery_worker worker --loglevel=info
```

#### 3. Start FastAPI Server

```bash
# In another terminal, activate virtual environment
venv\Scripts\activate

# Start development server with auto-reload
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

#### 4. (Optional) Start Prometheus

```bash
# If monitoring is set up
prometheus --config.file=prometheus.yml
```


## API Documentation

Once the FastAPI server is running, access the interactive API documentation:

- **Swagger UI:** http://localhost:8000/docs
- **ReDoc:** http://localhost:8000/redoc


### Key Endpoints

| Method | Endpoint              | Description                                        |
|--------|-----------------------|----------------------------------------------------|
| POST   | /api/v1/jobs          | Create a new media processing job (with operations)|
| GET    | /api/v1/jobs/{job_id} | Get job status, operations, result keys, metadata  |
| GET    | /api/v1/upload-url    | Generate pre-signed S3 upload URL                  |
| GET    | /health               | Health check endpoint                              |


## Testing

The test suite (`pytest`) is not part of the runtime repository. It will be
introduced alongside Week 4 (load testing with concurrent uploads), when a
`tests/` directory and coverage reporting are added back.


## Contributing

1. Fork the repository
2. Create a feature branch (`git checkout -b feature/amazing-feature`)
3. Commit your changes (`git commit -m 'Add amazing feature'`)
4. Push to the branch (`git push origin feature/amazing-feature`)
5. Open a Pull Request


## License

This project is proprietary and confidential. All rights reserved.

---


## Support

For questions or issues, please contact the development team or open an issue in the repository.
