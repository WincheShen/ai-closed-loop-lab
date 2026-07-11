#!/usr/bin/env python3
"""Transfer file to NAS via SSH."""
import subprocess
import sys

# Read local file
with open('src/webhook_listener/server.py', 'r') as f:
    content = f.read()

# Escape for shell
escaped_content = content.replace("'", "'\\''")

# SSH command to write file
ssh_cmd = f"""
sshpass -p 'Shen.Yun.88' ssh -o StrictHostKeyChecking=no kingSY_9@192.168.3.73 "cat > /tmp/server_new.py << 'EOF'
{escaped_content}
EOF
"""

# Execute
result = subprocess.run(ssh_cmd, shell=True, capture_output=True, text=True)
print(result.stdout)
if result.stderr:
    print("STDERR:", result.stderr)
sys.exit(result.returncode)
