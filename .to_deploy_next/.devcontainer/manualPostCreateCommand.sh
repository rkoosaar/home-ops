#!/usr/bin/env bash
set -e
set -o noglob

# # Install Krew plugin for kubectl
# krew install krew

# Add hooks into fish
tee /home/vscode/.config/fish/conf.d/hooks.fish > /dev/null <<EOF
if status is-interactive
    direnv hook fish | source
    starship init fish | source
    set -gx PATH $PATH $HOME/.krew/bin
end
EOF



# Setup fisher plugin manager for fish and install plugins
/usr/bin/fish -c "
krew install krew
fish_add_path ~/.krew/bin
kubectl krew install volsync
kubectl krew install browse-pvc

"



# /home/vscode/.config/fish/completions

# krew install krew
# fish_add_path ~/.krew/bin
