# AIfred Intelligence - the Node.js the interface is built with (sourced, needs PROJECT_DIR).
#
# Sourced by build-frontend.sh and install-all.sh. A Node installed into the project folder
# (install-all.sh, choice "l": frontend/node, version from frontend/.node-version) comes first on
# PATH; otherwise the system's Node is used.

NODE_LOCAL_DIR="$PROJECT_DIR/frontend/node"
NODE_VERSION="$(tr -d '[:space:]' < "$PROJECT_DIR/frontend/.node-version")"

if [ -x "$NODE_LOCAL_DIR/bin/node" ]; then
    export PATH="$NODE_LOCAL_DIR/bin:$PATH"
fi

# Whether node and npm are there and new enough for the build: Vite 8 wants ^20.19 or >= 22.12.
node_usable() {
    command -v node &>/dev/null && command -v npm &>/dev/null && node -e '
        const [major, minor] = process.versions.node.split(".").map(Number);
        process.exit((major === 20 && minor >= 19) || (major === 22 && minor >= 12) || major > 22 ? 0 : 1);
    '
}
