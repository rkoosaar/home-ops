# manual update


set app "budimanjojo/talhelper!!?as=talhelper&type=script"
curl -fsSL "https://i.jpillora.com/$app" | bash

set app "siderolabs/talos!!?as=talosctl&type=script"
curl -fsSL "https://i.jpillora.com/$app" | bash


apk add --no-cache \
    --repository=https://dl-cdn.alpinelinux.org/alpine/edge/community \
        flux kubectl kustomize go-task sops k9s
