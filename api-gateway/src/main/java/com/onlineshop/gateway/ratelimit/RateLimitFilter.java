package com.onlineshop.gateway.ratelimit;

import tools.jackson.databind.ObjectMapper;
import com.onlineshop.gateway.dto.ErrorResponse;
import com.onlineshop.gateway.filter.RequestAttributeKeys;
import com.onlineshop.gateway.metrics.GatewayMetrics;
import com.onlineshop.gateway.util.CorsHeaders;
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

@Component
@Slf4j
@Order(Ordered.HIGHEST_PRECEDENCE + 1)
@ConditionalOnProperty(name = "gateway.ratelimit.enabled", havingValue = "true", matchIfMissing = true)
public class RateLimitFilter extends OncePerRequestFilter {

    private final ObjectMapper objectMapper;
    private final GatewayMetrics metrics;
    private final RateLimitService rateLimitService;

    public RateLimitFilter(
            ObjectMapper objectMapper,
            GatewayMetrics metrics,
            RateLimitService rateLimitService) {
        this.objectMapper = objectMapper;
        this.metrics = metrics;
        this.rateLimitService = rateLimitService;
    }

    @Override
    protected void doFilterInternal(HttpServletRequest request, HttpServletResponse response, FilterChain filterChain)
            throws ServletException, IOException {

        String path = request.getRequestURI();

        // Skip rate limiting for health checks and CORS preflights
        if (path.startsWith("/actuator") || "OPTIONS".equalsIgnoreCase(request.getMethod())) {
            filterChain.doFilter(request, response);
            return;
        }

        Object userIdAttr = request.getAttribute(RequestAttributeKeys.USER_ID);
        String userId = userIdAttr != null ? userIdAttr.toString() : null;
        boolean isAuthenticated = userId != null;

        boolean allowed = isAuthenticated
                ? rateLimitService.tryConsumeAuthenticated(userId)
                : rateLimitService.tryConsumeAnonymous(request);

        if (allowed) {
            filterChain.doFilter(request, response);
        } else {
            String clientKey = userId != null ? "user:" + userId : "ip:" + rateLimitService.resolveClientIp(request);
            metrics.incrementRateLimitRejections();
            log.warn("Rate limit exceeded for client: {}", clientKey);
            sendTooManyRequestsResponse(request, response, "Rate limit exceeded. Please try again later.", path);
        }
    }

    private void sendTooManyRequestsResponse(HttpServletRequest request, HttpServletResponse response, String detail, String path)
            throws IOException {
        CorsHeaders.apply(request, response);
        ErrorResponse errorResponse = ErrorResponse.tooManyRequests(detail, path);

        response.setStatus(429);
        response.setContentType(MediaType.APPLICATION_JSON_VALUE);
        response.getWriter().write(objectMapper.writeValueAsString(errorResponse));
    }
}