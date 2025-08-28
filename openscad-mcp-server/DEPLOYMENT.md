# OpenSCAD MCP Server - Production Deployment Guide

## Version 1.0.0

This guide provides comprehensive instructions for deploying the OpenSCAD MCP Server in production environments.

## Table of Contents

1. [Prerequisites](#prerequisites)
2. [Installation Methods](#installation-methods)
3. [Configuration](#configuration)
4. [Security Considerations](#security-considerations)
5. [Deployment Checklist](#deployment-checklist)
6. [Monitoring and Maintenance](#monitoring-and-maintenance)
7. [Troubleshooting](#troubleshooting)
8. [Rollback Procedures](#rollback-procedures)

## Prerequisites

### System Requirements

- **Python**: 3.8 or higher
- **OpenSCAD**: Latest stable version (2021.01 or newer)
- **Operating System**: Linux, macOS, or Windows
- **Memory**: Minimum 2GB RAM recommended
- **Disk Space**: At least 500MB free space

### OpenSCAD Installation

Ensure OpenSCAD is installed and accessible from the command line:

```bash
# Verify OpenSCAD installation
openscad --version

# Expected output format:
# OpenSCAD version 2021.01
```

#### Platform-Specific Installation:

**Ubuntu/Debian:**
```bash
sudo apt-get update
sudo apt-get install openscad
```

**macOS:**
```bash
brew install openscad
```

**Windows:**
Download from [openscad.org](https://openscad.org/downloads.html) and ensure it's added to PATH.

## Installation Methods

### Method 1: Install from PyPI (Recommended for Production)

```bash
# Using pip
pip install openscad-mcp-server==1.0.0

# Using uv (faster alternative)
uv pip install openscad-mcp-server==1.0.0
```

### Method 2: Install from Distribution Package

```bash
# Install from wheel (recommended)
pip install openscad_mcp_server-1.0.0-py3-none-any.whl

# Install from source distribution
pip install openscad_mcp_server-1.0.0.tar.gz
```

### Method 3: Install in Virtual Environment (Best Practice)

```bash
# Create virtual environment
python -m venv openscad-mcp-env
source openscad-mcp-env/bin/activate  # On Windows: .\openscad-mcp-env\Scripts\activate

# Install the server
pip install openscad-mcp-server==1.0.0

# Install with specific dependencies
pip install fastmcp==2.11.3 pydantic==2.11.7 Pillow pyyaml
```

## Configuration

### Environment Variables

Create a `.env` file in your deployment directory:

```bash
# OpenSCAD Configuration
OPENSCAD_BINARY=/usr/bin/openscad  # Path to OpenSCAD binary
OPENSCAD_TIMEOUT=30                 # Timeout for rendering operations (seconds)
OPENSCAD_MAX_THREADS=4              # Maximum parallel rendering threads

# Server Configuration
MCP_SERVER_PORT=3000                # Port for MCP server
MCP_SERVER_HOST=0.0.0.0            # Host binding (use 127.0.0.1 for local only)
MCP_LOG_LEVEL=INFO                 # Logging level (DEBUG, INFO, WARNING, ERROR)

# Resource Limits
MAX_FILE_SIZE=10485760              # Maximum SCAD file size (10MB)
MAX_RENDER_RESOLUTION=2048          # Maximum render resolution
TEMP_DIR=/tmp/openscad-mcp         # Temporary directory for rendering

# Security
ENABLE_FILE_VALIDATION=true         # Enable input file validation
ALLOWED_FILE_EXTENSIONS=.scad,.json # Allowed file extensions
DISABLE_EXTERNAL_INCLUDES=true      # Disable external file includes in SCAD
```

### MCP Configuration

Add to your MCP client configuration (`mcp.json` or equivalent):

```json
{
  "mcpServers": {
    "openscad": {
      "command": "python",
      "args": ["-m", "openscad_mcp.server"],
      "env": {
        "OPENSCAD_BINARY": "/usr/bin/openscad",
        "MCP_LOG_LEVEL": "INFO"
      }
    }
  }
}
```

## Security Considerations

### 1. Input Validation

- All SCAD files are validated before processing
- File size limits are enforced
- Potentially dangerous OpenSCAD features can be disabled

### 2. Resource Limits

Configure resource limits to prevent DoS attacks:

```bash
# Set ulimits for the service user
ulimit -t 300  # CPU time limit (5 minutes)
ulimit -m 2097152  # Memory limit (2GB)
ulimit -f 104857600  # File size limit (100MB)
```

### 3. Network Security

- Bind to localhost (127.0.0.1) if external access is not required
- Use TLS/SSL for network communication in production
- Implement rate limiting for API endpoints

### 4. File System Security

```bash
# Create dedicated user for the service
sudo useradd -r -s /bin/false openscad-mcp

# Set appropriate permissions
sudo chown -R openscad-mcp:openscad-mcp /var/lib/openscad-mcp
sudo chmod 750 /var/lib/openscad-mcp
```

## Deployment Checklist

### Pre-Deployment

- [ ] Verify Python version (3.8+)
- [ ] Install and test OpenSCAD
- [ ] Review and configure environment variables
- [ ] Set up logging directory with appropriate permissions
- [ ] Configure resource limits
- [ ] Review security settings

### Installation

- [ ] Create virtual environment
- [ ] Install openscad-mcp-server package
- [ ] Verify all dependencies are installed
- [ ] Test basic server startup
- [ ] Validate MCP tool availability

### Post-Deployment

- [ ] Run smoke tests
- [ ] Verify logging is working
- [ ] Test all MCP tools
- [ ] Monitor resource usage
- [ ] Document deployment configuration

## Monitoring and Maintenance

### Health Checks

Create a health check script:

```python
#!/usr/bin/env python
import asyncio
from openscad_mcp.server import check_health

async def main():
    health = await check_health()
    if health['status'] == 'healthy':
        print("✓ Server is healthy")
        return 0
    else:
        print(f"✗ Server unhealthy: {health['error']}")
        return 1

if __name__ == "__main__":
    exit(asyncio.run(main()))
```

### Logging

Configure log rotation:

```bash
# /etc/logrotate.d/openscad-mcp
/var/log/openscad-mcp/*.log {
    daily
    rotate 14
    compress
    delaycompress
    notifempty
    create 0640 openscad-mcp openscad-mcp
    sharedscripts
    postrotate
        systemctl reload openscad-mcp || true
    endscript
}
```

### Systemd Service

Create `/etc/systemd/system/openscad-mcp.service`:

```ini
[Unit]
Description=OpenSCAD MCP Server
After=network.target

[Service]
Type=simple
User=openscad-mcp
Group=openscad-mcp
WorkingDirectory=/var/lib/openscad-mcp
Environment="PATH=/usr/local/bin:/usr/bin"
ExecStart=/usr/local/bin/python -m openscad_mcp.server
Restart=on-failure
RestartSec=10
StandardOutput=journal
StandardError=journal

# Security
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=strict
ProtectHome=true
ReadWritePaths=/var/lib/openscad-mcp /tmp/openscad-mcp

[Install]
WantedBy=multi-user.target
```

Enable and start the service:

```bash
sudo systemctl daemon-reload
sudo systemctl enable openscad-mcp
sudo systemctl start openscad-mcp
sudo systemctl status openscad-mcp
```

## Troubleshooting

### Common Issues

#### 1. OpenSCAD Not Found

```bash
# Check OpenSCAD installation
which openscad
openscad --version

# Update environment variable
export OPENSCAD_BINARY=$(which openscad)
```

#### 2. Permission Denied Errors

```bash
# Check file permissions
ls -la /var/lib/openscad-mcp
ls -la /tmp/openscad-mcp

# Fix permissions
sudo chown -R openscad-mcp:openscad-mcp /var/lib/openscad-mcp
sudo chmod 755 /var/lib/openscad-mcp
```

#### 3. High Memory Usage

```bash
# Monitor memory usage
ps aux | grep openscad
top -p $(pgrep -f openscad_mcp)

# Adjust resource limits in environment
OPENSCAD_MAX_THREADS=2
OPENSCAD_TIMEOUT=20
```

### Debug Mode

Enable debug logging for troubleshooting:

```bash
# Set debug level
export MCP_LOG_LEVEL=DEBUG

# Run server in foreground
python -m openscad_mcp.server --debug
```

### Log Analysis

```bash
# View recent logs
journalctl -u openscad-mcp -n 100

# Follow logs in real-time
journalctl -u openscad-mcp -f

# Search for errors
journalctl -u openscad-mcp | grep ERROR
```

## Rollback Procedures

### Version Rollback

```bash
# Stop the service
sudo systemctl stop openscad-mcp

# Uninstall current version
pip uninstall openscad-mcp-server

# Install previous version
pip install openscad-mcp-server==0.9.0  # Replace with previous version

# Restart service
sudo systemctl start openscad-mcp
```

### Configuration Rollback

```bash
# Backup current configuration
cp /var/lib/openscad-mcp/.env /var/lib/openscad-mcp/.env.backup

# Restore previous configuration
cp /var/lib/openscad-mcp/.env.previous /var/lib/openscad-mcp/.env

# Restart service
sudo systemctl restart openscad-mcp
```

### Emergency Recovery

```bash
#!/bin/bash
# emergency-recovery.sh

echo "Starting emergency recovery..."

# Stop service
sudo systemctl stop openscad-mcp

# Clear temporary files
rm -rf /tmp/openscad-mcp/*

# Reset to known good state
cd /var/lib/openscad-mcp
git checkout stable  # If using git for config management

# Reinstall stable version
pip install --force-reinstall openscad-mcp-server==1.0.0

# Start service
sudo systemctl start openscad-mcp

# Verify health
python -m openscad_mcp.server --health-check
```

## Performance Optimization

### Caching Configuration

```python
# config/cache.yaml
cache:
  enabled: true
  type: redis  # or memory
  ttl: 3600  # Cache TTL in seconds
  max_size: 1000  # Maximum cache entries
  
redis:
  host: localhost
  port: 6379
  db: 0
  password: null
```

### Resource Tuning

```bash
# Optimize for high-load scenarios
OPENSCAD_MAX_THREADS=8      # Increase for multi-core systems
OPENSCAD_TIMEOUT=60         # Increase for complex models
MAX_RENDER_RESOLUTION=4096  # Increase for high-quality renders
```

## Support and Resources

- **Documentation**: [README.md](README.md)
- **API Reference**: [API.md](API.md)
- **Changelog**: [CHANGELOG.md](CHANGELOG.md)
- **Issues**: GitHub Issues (if applicable)
- **MCP Documentation**: [modelcontextprotocol.io](https://modelcontextprotocol.io)

## License

This software is released under the MIT License. See LICENSE file for details.

---

**Last Updated**: 2025-08-25  
**Version**: 1.0.0  
**Status**: Production Ready