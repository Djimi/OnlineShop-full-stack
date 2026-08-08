package com.onlineshop.gateway.config;

import java.time.Duration;

import org.junit.jupiter.api.Test;

import io.github.resilience4j.timelimiter.TimeLimiter;
import io.github.resilience4j.timelimiter.TimeLimiterRegistry;

import static org.assertj.core.api.Assertions.assertThat;

class ResilienceConfigTest {

    @Test
    void authServiceTimeLimiterUsesFiveSecondTimeout() {
        TimeLimiterRegistry registry = new ResilienceConfig().timeLimiterRegistry();

        TimeLimiter timeLimiter = registry.timeLimiter("authService");

        assertThat(timeLimiter.getTimeLimiterConfig().getTimeoutDuration())
                .isEqualTo(Duration.ofSeconds(5));
    }
}
