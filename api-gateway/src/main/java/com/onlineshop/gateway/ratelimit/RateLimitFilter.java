package com.onlineshop.gateway.ratelimit;

import tools.jackson.databind.ObjectMapper;
import com.onlineshop.gateway.dto.ErrorResponse;
import com.onlineshop.gateway.metrics.GatewayMetrics;
import io.github.bucket4j.Bandwidth;
import io.github.bucket4j.Bucket;
import io.github.bucket4j.BucketConfiguration;
import io.github.bucket4j.Refill;
import io.github.bucket4j.distributed.proxy.ProxyManager;
import com.onlineshop.gateway.filter.RequestAttributeKeys;
import jakarta.servlet.FilterChain;
import jakarta.servlet.ServletException;
import jakarta.servlet.http.HttpServletRequest;
import jakarta.servlet.http.HttpServletResponse;
import lombok.extern.slf4j.Slf4j;
import org.springframework.boot.autoconfigure.condition.ConditionalOnProperty;
import org.springframework.core.Ordered;
import org.springframework.core.annotation.Order;
import org.springframework.http.MediaType;
import org.springframework.stereotype.Component;
import org.springframework.web.filter.OncePerRequestFilter;

import java.io.IOException;
import java.time.Duration;

@Component
@Slf4j
@Order(Ordered.HIGHEST_PRECEDENCE + 1)
@ConditionalOnProperty(name = "gateway.ratelimit.enabled", havingValue = "true", matchIfMissing = true)
public class RateLimitFilter extends OncePerRequestFilter {

    private final ObjectMapper objectMapper;
    private final GatewayMetrics metrics;
    private final ProxyManager<String> proxyManager;
    private final RateLimitConfigProperties rateLimitConfigProperties;
    private volatile boolean loggedRedisDown;

    public RateLimitFilter(
            ObjectMapper objectMapper,
            GatewayMetrics metrics,
            ProxyManager<String> proxyManager,
            RateLimitConfigProperties rateLimitConfigProperties) {
        this.objectMapper = objectMapper;
        this.metrics = metrics;
        this.proxyManager = proxyManager;
        this.rateLimitConfigProperties = rateLimitConfigProperties;
    }

    @Override
    protected void doFilterInternal(HttpServletRequest request, HttpServletResponse response, FilterChain filterChain)
            throws ServletException, IOException {

        String path = request.getRequestURI();

        // Skip rate limiting for auth endpoints and health checks
        if (path.startsWith("/auth") || path.startsWith("/actuator")) {
            filterChain.doFilter(request, response);
            return;
        }

        Object userIdAttr = request.getAttribute(RequestAttributeKeys.USER_ID);
        String userId = userIdAttr != null ? userIdAttr.toString() : null;
        String clientKey = userId != null ? "user:" + userId : "ip:" + getClientIP(request);

        boolean isAuthenticated = userId != null;

        BucketConfiguration config = isAuthenticated
                ? createAuthenticatedConfig()
                : createAnonymousConfig();

        try {
            Bucket bucket = proxyManager.builder().build(clientKey, () -> config);

            if (bucket.tryConsume(1)) {
                filterChain.doFilter(request, response);
            } else {
                metrics.incrementRateLimitRejections();
                log.warn("Rate limit exceeded for client: {}", clientKey);
                sendTooManyRequestsResponse(response, "Rate limit exceeded. Please try again later.", path);
            }
        } catch (Exception e) {
            if (!loggedRedisDown) {
                loggedRedisDown = true;
                log.warn("Rate limiter unavailable (Redis down?), failing open: {}", e.getMessage());
            }
            filterChain.doFilter(request, response);
        }
    }

    private BucketConfiguration createAnonymousConfig() {
        Bandwidth limit = Bandwidth.classic(
                rateLimitConfigProperties.anonymous().requestsPerMinute(),
                Refill.intervally(rateLimitConfigProperties.anonymous().requestsPerMinute(), Duration.ofMinutes(1))
        );
        return BucketConfiguration.builder().addLimit(limit).build();
    }

    private BucketConfiguration createAuthenticatedConfig() {
        Bandwidth limit = Bandwidth.classic(
                rateLimitConfigProperties.authenticated().requestsPerMinute(),
                Refill.intervally(rateLimitConfigProperties.authenticated().requestsPerMinute(), Duration.ofMinutes(1))
        );
        return BucketConfiguration.builder().addLimit(limit).build();
    }

    private String getClientIP(HttpServletRequest request) {
        String xForwardedFor = request.getHeader("X-Forwarded-For");
        if (xForwardedFor != null && !xForwardedFor.isEmpty()) {
            return xForwardedFor.split(",")[0].trim();
        }
        return request.getRemoteAddr();
    }

    private void sendTooManyRequestsResponse(HttpServletResponse response, String detail, String path)
            throws IOException {
        ErrorResponse errorResponse = ErrorResponse.tooManyRequests(detail, path);

        response.setStatus(429);
        response.setContentType(MediaType.APPLICATION_JSON_VALUE);
        response.getWriter().write(objectMapper.writeValueAsString(errorResponse));
    }
}
