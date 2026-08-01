package com.onlineshop.gateway.config;

import java.net.http.HttpClient;
import java.time.Duration;

import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;
import org.springframework.http.client.JdkClientHttpRequestFactory;
import org.springframework.http.converter.HttpMessageNotReadableException;
import org.springframework.web.client.RestClient;

import io.github.resilience4j.bulkhead.Bulkhead;
import io.github.resilience4j.bulkhead.BulkheadConfig;
import io.github.resilience4j.bulkhead.BulkheadRegistry;
import io.github.resilience4j.circuitbreaker.CircuitBreaker;
import io.github.resilience4j.circuitbreaker.CircuitBreakerConfig;
import io.github.resilience4j.circuitbreaker.CircuitBreakerRegistry;
import io.github.resilience4j.retry.Retry;
import io.github.resilience4j.retry.RetryConfig;
import io.github.resilience4j.retry.RetryRegistry;
import io.github.resilience4j.timelimiter.TimeLimiter;
import io.github.resilience4j.timelimiter.TimeLimiterConfig;
import io.github.resilience4j.timelimiter.TimeLimiterRegistry;
import lombok.extern.slf4j.Slf4j;

@Configuration
@Slf4j
public class ResilienceConfig {

    /**
     * Configures RestClient with JDK's native HttpClient.
     *
     * Timeouts:
     * - Connection timeout: 5 seconds (time to establish connection)
     * - Read timeout: 5 seconds (time to read response)
     *
     * Note: Resilience4j TimeLimiter (4s) triggers before these HTTP-level
     * timeouts (5s), providing the primary timeout control. The HTTP-level
     * timeouts serve as a safety net if the TimeLimiter fails to trigger.
     */
    @Bean
    public RestClient restClient() {
        // Use Java's native HttpClient (Java 11+) with connection timeout
        HttpClient httpClient = HttpClient.newBuilder()
                .connectTimeout(Duration.ofSeconds(5))
                .build();

        // Create request factory with read timeout
        JdkClientHttpRequestFactory requestFactory = new JdkClientHttpRequestFactory(httpClient);
        requestFactory.setReadTimeout(Duration.ofSeconds(5));

        // Build RestClient with configured factory
        return RestClient.builder()
                .requestFactory(requestFactory)
                .build();
    }

    @Bean
    public CircuitBreaker authServiceCircuitBreaker() {
        CircuitBreakerConfig config = CircuitBreakerConfig.custom()
                .slidingWindowSize(3)
                .failureRateThreshold(50)
                .waitDurationInOpenState(Duration.ofSeconds(30))
                .permittedNumberOfCallsInHalfOpenState(3)
                // Ignore parsing errors - they're not service failures, just bad responses
                // Don't trigger circuit breaker or count towards failure rate
                .ignoreExceptions(HttpMessageNotReadableException.class)
                .build();

        CircuitBreakerRegistry registry = CircuitBreakerRegistry.of(config);
        CircuitBreaker circuitBreaker = registry.circuitBreaker("authService");

        // Log parsing errors at warn level for monitoring
        circuitBreaker.getEventPublisher()
                .onIgnoredError(event -> log.warn(
                        "Auth service returned unparseable response: {}",
                        event.getThrowable().getMessage()));

        return circuitBreaker;
    }

    @Bean
    public Retry authServiceRetry() {
        RetryConfig config = RetryConfig.custom()
                .maxAttempts(3)
                .waitDuration(Duration.ofMillis(500))
                // Don't retry on parsing errors - they won't succeed on retry
                .ignoreExceptions(HttpMessageNotReadableException.class)
                .build();

        RetryRegistry registry = RetryRegistry.of(config);
        return registry.retry("authService");
    }

    @Bean
    public TimeLimiter authServiceTimeLimiter() {
        TimeLimiterConfig config = TimeLimiterConfig.custom()
                .timeoutDuration(Duration.ofSeconds(4))
                .build();

        TimeLimiterRegistry registry = TimeLimiterRegistry.of(config);
        return registry.timeLimiter("authService");
    }

    /**
     * Configures Bulkhead pattern for Auth Service.
     *
     * The Bulkhead pattern limits the number of concurrent calls to prevent
     * resource exhaustion and cascading failures. This implementation uses
     * a semaphore-based approach which is optimal for virtual threads.
     *
     * Configuration Details:
     * - maxConcurrentCalls: 10 - Maximum concurrent calls allowed (prevents overwhelming target service)
     * - maxWaitDuration: 5 seconds - Maximum time to wait for a permit (prevents indefinite blocking)
     * - writableStackTraceEnabled: true - Captures stack traces for better debugging
     *
     * For Auth Service, we use a conservative limit (10 concurrent) because:
     * 1. Each auth validation call performs expensive token verification
     * 2. Auth service is a critical dependency (circuit breaker helps here too)
     * 3. Virtual threads handle waiting gracefully without thread pool overhead
     */
    @Bean
    public Bulkhead authServiceBulkhead() {
        BulkheadConfig config = BulkheadConfig.custom()
                // Maximum number of concurrent calls allowed
                // This prevents overwhelming the auth service with too many simultaneous requests
                // With virtual threads, we can safely allow more concurrent calls compared to platform threads
                .maxConcurrentCalls(200)

                // Maximum duration to wait for a permit to execute
                // If a permit isn't available within this time, BulkheadFullException is thrown
                // Set to 5 seconds to align with our HTTP timeouts and allow graceful backpressure
                .maxWaitDuration(Duration.ofSeconds(5))

                // Enable detailed stack traces in exceptions
                // Useful for debugging when bulkhead is exhausted (BulkheadFullException)
                // Helps identify which part of the code triggered the bulkhead rejection
                .writableStackTraceEnabled(true)

                .build();

        BulkheadRegistry registry = BulkheadRegistry.of(config);
        return registry.bulkhead("authService");
    }
}
