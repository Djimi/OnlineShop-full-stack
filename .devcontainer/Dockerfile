# Dev Container workspace image for OnlineShop.
# Provides JDK 25, Node LTS, Docker CLI, Claude Code CLI, and common dev tools.
# Spring Boot services and the React frontend are run from inside this container
# against infra (Postgres, Redis, Kafka) running as sibling Compose services.
#
# We use the generic base image instead of the java-specific one because:
#   1. The java:1-21 image ships JDK 21 which conflicts with the JDK 25
#      installed by the devcontainer java feature — VS Code's Java extension
#      may pick up JDK 21 and fail to compile Java 25 source ("Main method
#      not found").
#   2. The java image sets NPM_CONFIG_PREFIX=/home/vscode/.npm-global which
#      is incompatible with nvm (installed by the devcontainer node feature).
#   3. With NPM_CONFIG_PREFIX set, `npm install -g` puts binaries in a
#      directory that's not on the PATH for interactive shells using nvm,
#      making the Claude Code CLI invisible.
#
# JDK 25 and Node LTS are installed cleanly via devcontainer features
# (see devcontainer.json).
FROM mcr.microsoft.com/devcontainers/base:bookworm

# --- OS tooling -------------------------------------------------------------
# Install common CLI tools for development and database inspection.
RUN apt-get update \
    && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
        curl \
        jq \
        git \
        bash-completion \
        postgresql-client \
        redis-tools \
        netcat-openbsd \
        unzip \
        procps \
    && rm -rf /var/lib/apt/lists/*

# --- Node LTS + Claude Code CLI --------------------------------------------
# Node is installed via the devcontainer "node" feature (see devcontainer.json).
# Claude Code CLI is installed in postCreateCommand so that the image build
# stays cacheable and the CLI can be updated without rebuilding the image.

# --- Non-root user ----------------------------------------------------------
# Base image already provides the "vscode" user (uid 1000).
USER vscode
WORKDIR /workspaces/OnlineShop-full-stack
