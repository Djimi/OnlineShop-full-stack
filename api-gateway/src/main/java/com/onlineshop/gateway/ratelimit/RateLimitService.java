package com.onlineshop.gateway.ratelimit;

import io.github.bucket4j.Bandwidth;
import io.github.bucket4j.Bucket;
import io.github.bucket4j.BucketConfiguration;
import io.github.bucket4j.Refill;
import io.github.bucket4j.distributed.proxy.ProxyManager;
import jakarta.servlet.http.HttpServletRequest;
import lombok.extern.slf4j.Slf4j;
import org.springframework.boot.autoconfigure.condition.ConditionalOnProperty;
import org.springframework.stereotype.Component;

import java.time.Duration;
import java.util.List;

@Component
@Slf4j
@ConditionalOnProperty(name = "gateway.ratelimit.enabled", havingValue = "true", matchIfMissing = true)
public class RateLimitService {

    private final ProxyManager<String> proxyManager;
    private final RateLimitConfigProperties rateLimitConfigProperties;
    private volatile boolean loggedRedisDown;

    public RateLimitService(
            ProxyManager<String> proxyManager,
            RateLimitConfigProperties rateLimitConfigProperties) {
        this.proxyManager = proxyManager;
        this.rateLimitConfigProperties = rateLimitConfigProperties;
    }

    public boolean tryConsumeAnonymous(HttpServletRequest request) {
        return tryConsume("ip:" + resolveClientIp(request), createAnonymousConfig());
    }

    public boolean tryConsumeAuthenticated(String userId) {
        return tryConsume("user:" + userId, createAuthenticatedConfig());
    }

    public String resolveClientIp(HttpServletRequest request) {
        String remoteAddr = request.getRemoteAddr();
        List<String> trustedProxies = rateLimitConfigProperties.trustedProxies();
        if (trustedProxies != null && trustedProxies.contains(remoteAddr)) {
            String xForwardedFor = request.getHeader("X-Forwarded-For");
            if (xForwardedFor != null && !xForwardedFor.isEmpty()) {
                return xForwardedFor.split(",")[0].trim();
            }
        }
        return remoteAddr;
    }

    private boolean tryConsume(String key, BucketConfiguration config) {
        try {
            Bucket bucket = proxyManager.builder().build(key, () -> config);
            return bucket.tryConsume(1);
        } catch (Exception e) {
            if (!loggedRedisDown) {
                loggedRedisDown = true;
                log.warn("Rate limiter unavailable (Redis down?), failing open: {}", e.getMessage());
            }
            return true;
        }
    }

    private BucketConfiguration createAnonymousConfig() {
        return BucketConfiguration.builder().addLimit(createLimit(
                rateLimitConfigProperties.anonymous().burst(),
                rateLimitConfigProperties.anonymous().requestsPerMinute())).build();
    }

    private BucketConfiguration createAuthenticatedConfig() {
        return BucketConfiguration.builder().addLimit(createLimit(
                rateLimitConfigProperties.authenticated().burst(),
                rateLimitConfigProperties.authenticated().requestsPerMinute())).build();
    }

    private Bandwidth createLimit(int burst, int requestsPerMinute) {
        return Bandwidth.classic(burst, Refill.greedy(requestsPerMinute, Duration.ofMinutes(1)));
    }
}