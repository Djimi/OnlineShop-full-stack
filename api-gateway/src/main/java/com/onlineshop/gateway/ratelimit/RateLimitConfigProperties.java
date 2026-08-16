package com.onlineshop.gateway.ratelimit;

import org.springframework.boot.context.properties.ConfigurationProperties;

import java.util.List;

@ConfigurationProperties(prefix = "gateway.ratelimit")
public record RateLimitConfigProperties(
        LimitSettings anonymous,
        LimitSettings authenticated,
        List<String> trustedProxies
) {
    public record LimitSettings(int requestsPerMinute, int burst) {
    }
}
