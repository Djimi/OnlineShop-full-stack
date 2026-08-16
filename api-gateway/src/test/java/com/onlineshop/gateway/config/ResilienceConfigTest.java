package com.onlineshop.gateway.config;

import java.time.Duration;

import org.junit.jupiter.api.Test;
import org.springframework.http.HttpInputMessage;
import org.springframework.http.converter.HttpMessageNotReadableException;

import io.github.resilience4j.bulkhead.Bulkhead;
import io.github.resilience4j.bulkhead.BulkheadRegistry;
import io.github.resilience4j.circuitbreaker.CircuitBreaker;
import io.github.resilience4j.circuitbreaker.CircuitBreakerRegistry;
import io.github.resilience4j.timelimiter.TimeLimiter;
import io.github.resilience4j.timelimiter.TimeLimiterRegistry;

import static org.assertj.core.api.Assertions.assertThat;
import static org.mockito.Mockito.mock;

class ResilienceConfigTest {

    @Test
    void authServiceTimeLimiterUsesFiveSecondTimeout() {
        TimeLimiterRegistry registry = new ResilienceConfig().timeLimiterRegistry();

        TimeLimiter timeLimiter = registry.timeLimiter("authService");

        assertThat(timeLimiter.getTimeLimiterConfig().getTimeoutDuration())
                .isEqualTo(Duration.ofSeconds(5));
    }

    @Test
    void authServiceCircuitBreakerUsesConfiguredSettings() {
        CircuitBreakerRegistry registry = new ResilienceConfig().circuitBreakerRegistry();

        CircuitBreaker circuitBreaker = registry.circuitBreaker("authService");

        assertThat(circuitBreaker.getCircuitBreakerConfig().getSlidingWindowSize()).isEqualTo(3);
        assertThat(circuitBreaker.getCircuitBreakerConfig().getWaitIntervalFunctionInOpenState().apply(1))
                .isEqualTo(Duration.ofSeconds(30).toMillis());
        assertThat(circuitBreaker.getCircuitBreakerConfig().getIgnoreExceptionPredicate()
                .test(new HttpMessageNotReadableException("unparseable", null, mock(HttpInputMessage.class)))).isTrue();
        assertThat(circuitBreaker.getCircuitBreakerConfig().getIgnoreExceptionPredicate()
                .test(new IllegalStateException("real failure"))).isFalse();
    }

    @Test
    void authServiceBulkheadUsesConfiguredSettings() {
        BulkheadRegistry registry = new ResilienceConfig().bulkheadRegistry();

        Bulkhead bulkhead = registry.bulkhead("authService");

        assertThat(bulkhead.getBulkheadConfig().getMaxConcurrentCalls()).isEqualTo(200);
    }
}
