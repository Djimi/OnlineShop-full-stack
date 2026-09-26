/**
 * Environment Configuration for Performance Tests
 *
 * Usage: k6 run -e ENV=local smoke-1vu.js
 */

export const ENVIRONMENTS = {
    local: {
        authServiceUrl: 'http://localhost:9001',
        name: 'Local Development',
    },
    docker: {
        authServiceUrl: 'http://auth-service:9001',
        name: 'Docker Compose',
    },
};

/**
 * Get the current environment configuration
 * Defaults to 'local' if not specified
 */
export function getEnvironment() {
    const envName = __ENV.ENV || 'local';
    const env = ENVIRONMENTS[envName];

    if (!env) {
        console.error(`Unknown environment: ${envName}. Available: ${Object.keys(ENVIRONMENTS).join(', ')}`);
        throw new Error(`Unknown environment: ${envName}`);
    }

    console.log(`Using environment: ${env.name} (${envName})`);
    return env;
}

/**
 * Get the Auth service base URL
 */
export function getAuthServiceUrl() {
    const env = getEnvironment();
    return env.authServiceUrl;
}

