# Distributed_Media_Processing_Microservice


## Table of Contents

- [Executive Summary](#executive-summary)
- [Objectives](#objectives)
- [Technical Architecture](#technical-architecture)
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
- [Project Structure](#project-structure)
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

**Week 1: API & Storage**
- FastAPI scaffolding.
- Boto3 integration for S3 access.
- Pre-signed URL logic.
- Redis setup for job tracking.

**Week 2: Queue & Workers**
- RabbitMQ deployment.
- Celery integration with retry policies.
- Worker simulation.
- Error handling for network failures.

**Week 3: Media Logic**
- Pillow scripts for image transformations.
- FFmpeg integration for video transcoding.
- Thumbnail extraction.
- End-to-end local testing.

**Week 4: Infrastructure & Monitoring**
- Prometheus metrics integration.
- Load testing with concurrent uploads.
- Documentation + optimization.


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

Create a `.env` file in the project root:

```env
# AWS Configuration
AWS_ACCESS_KEY_ID=your_access_key_id
AWS_SECRET_ACCESS_KEY=your_secret_access_key
AWS_REGION=us-east-1
S3_BUCKET_NAME=your-bucket-name
CLOUDFRONT_DOMAIN=your-cloudfront-domain.cloudfront.net

# Redis Configuration
REDIS_HOST=localhost
REDIS_PORT=6379
REDIS_DB=0

# RabbitMQ Configuration
RABBITMQ_HOST=localhost
RABBITMQ_PORT=5672
RABBITMQ_USER=guest
RABBITMQ_PASS=guest

# Celery Configuration
CELERY_BROKER_URL=amqp://guest:guest@localhost:5672//
CELERY_RESULT_BACKEND=redis://localhost:6379/0

# Application Configuration
APP_HOST=0.0.0.0
APP_PORT=8000
APP_ENV=development

# Processing Configuration
MAX_IMAGE_SIZE=4096
MAX_VIDEO_DURATION=300
THUMBNAIL_SIZE=(320, 240)
```


## Project Structure

```
Distributed_Media_Processing_Microservice/
├── app/
│   ├── __init__.py
│   ├── main.py                 # FastAPI application entry point
│   ├── config.py               # Configuration and environment variables
│   ├── models/                 # Pydantic models
│   │   ├── __init__.py
│   │   ├── job.py
│   │   └── media.py
│   ├── routers/                # API endpoints
│   │   ├── __init__.py
│   │   ├── upload.py
│   │   ├── job.py
│   │   └── health.py
│   ├── services/               # Business logic
│   │   ├── __init__.py
│   │   ├── s3_service.py
│   │   ├── redis_service.py
│   │   ├── celery_service.py
│   │   └── processing/         # Media processing scripts
│   │       ├── __init__.py
│   │       ├── image_processor.py
│   │       └── video_processor.py
│   └── utils/                  # Helper utilities
│       ├── __init__.py
│       └── helpers.py
├── workers/
│   ├── __init__.py
│   └── celery_worker.py        # Celery worker tasks
├── tests/                      # Test suite
│   ├── __init__.py
│   ├── test_api.py
│   ├── test_processing.py
│   └── test_workers.py
├── requirements.txt            # Python dependencies
├── .env.example                # Example environment variables
├── .gitignore                  # Git ignore file
└── README.md                   # This file
```


## Running the Project

### Running Locally (Development)

#### 1. Start Infrastructure Services

Using Docker (recommended):

```bash
docker compose up -d
```

This starts Redis (localhost:6379) and RabbitMQ (localhost:5672, management UI at http://localhost:15672).

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

| Method | Endpoint              | Description                              |
|--------|-----------------------|------------------------------------------|
| POST   | /api/v1/jobs          | Create a new media processing job        |
| GET    | /api/v1/jobs/{job_id} | Get job status and details               |
| GET    | /api/v1/upload-url    | Generate pre-signed S3 upload URL        |
| GET    | /health               | Health check endpoint                    |
| GET    | /metrics              | Prometheus metrics (if enabled)          |


## Testing

### Run All Tests

```bash
pytest tests/ -v
```

### Run with Coverage

```bash
pytest tests/ --cov=app --cov-report=html
```

### Run Specific Test File

```bash
pytest tests/test_api.py -v
pytest tests/test_processing.py -v
pytest tests/test_workers.py -v
```


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