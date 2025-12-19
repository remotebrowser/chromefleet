# Dokku Deployment


Install [Podman](https://podman.io) and verify it:
```
sudo apt install -y podman
sudo podman run hello-world
```

Enable the Podman systemd socket and verify it:
```
sudo systemctl enable --now podman.socket
systemctl status podman.socket --no-pager
```

Override the socket path by running `sudo systemctl edit podman.socket` and editing the contents to:
```
[Socket]
ListenStream=
ListenStream=/run/podman.sock
SocketMode=0666
```

Reboot the machine.

Quick test (should display full Podman information and not throw any errors):
```
CONTAINER_HOST="unix:///run/podman.sock" podman --remote info
```

Run the test again to verify that the Podman socket permissions persist after reboot:

Another test:
```
CONTAINER_HOST="unix:///run/podman.sock" podman --remote run hello-world
```

Install and configure [Tailscale](https://tailscale.com) on the host machine:
```
curl -fsSL https://tailscale.com/install.sh | sh
sudo tailscale up
```

Verify that the machine appears on the [Tailscale admin console page](https://login.tailscale.com/admin/machines).

Install Dokku per [the official guide](https://dokku.com/docs/getting-started/installation):
```
wget -NP . https://dokku.com/install/v0.37.2/bootstrap.sh
sudo DOKKU_TAG=v0.37.2 bash bootstrap.sh
```

Add at least one SSH key for manual deployment.

Create the app:
```
dokku apps:create chromefleet
dokku ports:add chromefleet http:80:8300
dokku config:set chromefleet CONTAINER_HOST="unix:///run/podman.sock"
dokku docker-options:add chromefleet deploy "--cap-add=NET_ADMIN"
dokku docker-options:add chromefleet deploy "--cap-add=NET_RAW"
dokku docker-options:add chromefleet deploy "--device=/dev/net/tun:/dev/net/tun"
dokku docker-options:add chromefleet deploy,run "-v /run/podman.sock:/run/podman.sock"
```

Obtain the auth key from the [Tailscale admin console](https://login.tailscale.com/admin/machines/new-linux) and set it:
```
dokku config:set chromefleet TS_AUTHKEY=your-tailscale-auth-key
```

Set the domain (optional):
```
dokku domains:set chromefleet chromefleet.example.com
```

Then deploy Chrome Fleet manually to this Dokku machine.

Once deployed, test it by launching a machine:
```
curl chromefleet-ip-address/api/v1/start/xyz123
```

It should return the tailnet IP address for that machine. The Tailscale admin console should also show a node named `chromium-xyz123`.
