package com.onlineshop.gateway.filter;

import tools.jackson.databind.ObjectMapper;
import com.onlineshop.gateway.dto.ErrorResponse;
import com.onlineshop.gateway.dto.ValidateResponse;
import com.onlineshop.gateway.exception.GatewayTimeoutException;
import com.onlineshop.gateway.exception.InvalidTokenFormatException;
import com.onlineshop.gateway.exception.ServiceUnavailableException;
import com.onlineshop.gateway.service.AuthValidationService;
import com.onlineshop.gateway.validation.TokenSanitizer;
import jakarta.servlet.FilterChain;
import jakarta.servlet.ServletException;
import jakarta.servlet.http.HttpServletRequest;
import jakarta.servlet.http.HttpServletRequestWrapper;
import jakarta.servlet.http.HttpServletResponse;
import lombok.extern.slf4j.Slf4j;
import org.springframework.core.Ordered;
import org.springframework.core.annotation.Order;
import org.springframework.http.MediaType;
import org.springframework.stereotype.Component;
import org.springframework.web.filter.OncePerRequestFilter;

import java.io.IOException;
import java.util.Collections;
import java.util.Enumeration;
import java.util.HashSet;
import java.util.Map;
import java.util.Set;

@Component
@Slf4j
@Order(Ordered.HIGHEST_PRECEDENCE)
public class AuthenticationFilter extends OncePerRequestFilter {

    private final AuthValidationService authValidationService;
    private final ObjectMapper objectMapper;
    private final TokenSanitizer tokenSanitizer;

    private static final String AUTHORIZATION_HEADER = "Authorization";
    private static final String BEARER_PREFIX = "Bearer ";

    public AuthenticationFilter(
            AuthValidationService authValidationService,
            ObjectMapper objectMapper,
            TokenSanitizer tokenSanitizer) {
        this.authValidationService = authValidationService;
        this.objectMapper = objectMapper;
        this.tokenSanitizer = tokenSanitizer;
    }

    @Override
    protected void doFilterInternal(HttpServletRequest request, HttpServletResponse response, FilterChain filterChain)
            throws ServletException, IOException {

        // Skip CORS preflight OPTIONS requests
        if ("OPTIONS".equalsIgnoreCase(request.getMethod())) {
            filterChain.doFilter(request, response);
            return;
        }

        String path = request.getRequestURI();

        // Skip authentication for auth endpoints
        if (path.startsWith("/auth")) {
            filterChain.doFilter(request, response);
            return;
        }

        // Only authenticate /items/** requests
        if (!path.startsWith("/items")) {
            filterChain.doFilter(request, response);
            return;
        }

        String authHeader = request.getHeader(AUTHORIZATION_HEADER);

        if (authHeader == null || !authHeader.startsWith(BEARER_PREFIX)) {
            sendUnauthorizedResponse(request, response, "Missing or invalid Authorization header", path);
            return;
        }

        String token = authHeader.substring(BEARER_PREFIX.length());

        try {
            ValidateResponse validateResponse = authValidationService.validateToken(token);

            if (!validateResponse.isValid()) {
                sendUnauthorizedResponse(request, response, "Invalid or expired token", path);
                return;
            }

            // Sanitize username before adding to headers
            String sanitizedUsername = tokenSanitizer.sanitizeUsername(validateResponse.getUsername());

            // Add user info headers for downstream services
            HttpServletRequest wrappedRequest = new HeaderMapRequestWrapper(request, Map.of(
                    "X-User-Id", validateResponse.getUserId().toString(),
                    "X-Username", sanitizedUsername
            ));
            wrappedRequest.setAttribute(RequestAttributeKeys.USER_ID, validateResponse.getUserId().toString());

            log.debug("Authenticated request for user: {} (ID: {})",
                    sanitizedUsername, validateResponse.getUserId());

            filterChain.doFilter(wrappedRequest, response);

        } catch (InvalidTokenFormatException e) {
            log.warn("Invalid token format: {}", e.getMessage());
            sendBadRequestResponse(request, response, e.getMessage(), path);
        } catch (ServiceUnavailableException e) {
            log.error("Auth service unavailable: {}", e.getMessage());
            sendServiceUnavailableResponse(request, response, "Authentication service is temporarily unavailable", path);
        } catch (GatewayTimeoutException e) {
            log.error("Auth service timeout: {}", e.getMessage());
            sendGatewayTimeoutResponse(request, response, "Authentication service request timed out", path);
        } catch (Exception e) {
            log.error("Unexpected error during authentication: {}", e.getMessage(), e);
            sendBadGatewayResponse(request, response, "An unexpected error occurred during authentication", path);
        }
    }

    private void sendBadRequestResponse(HttpServletRequest request, HttpServletResponse response,
                                        String detail, String path) throws IOException {
        addCorsHeaders(request, response);
        ErrorResponse errorResponse = ErrorResponse.badRequest(detail, path);

        response.setStatus(HttpServletResponse.SC_BAD_REQUEST);
        response.setContentType(MediaType.APPLICATION_JSON_VALUE);
        response.getWriter().write(objectMapper.writeValueAsString(errorResponse));
    }

    private void sendUnauthorizedResponse(HttpServletRequest request, HttpServletResponse response,
                                          String detail, String path) throws IOException {
        addCorsHeaders(request, response);
        ErrorResponse errorResponse = ErrorResponse.unauthorized(detail, path);

        response.setStatus(HttpServletResponse.SC_UNAUTHORIZED);
        response.setContentType(MediaType.APPLICATION_JSON_VALUE);
        response.getWriter().write(objectMapper.writeValueAsString(errorResponse));
    }

    private void sendServiceUnavailableResponse(HttpServletRequest request, HttpServletResponse response,
                                                String detail, String path) throws IOException {
        addCorsHeaders(request, response);
        ErrorResponse errorResponse = ErrorResponse.serviceUnavailable(detail, path);

        response.setStatus(HttpServletResponse.SC_SERVICE_UNAVAILABLE);
        response.setContentType(MediaType.APPLICATION_JSON_VALUE);
        response.getWriter().write(objectMapper.writeValueAsString(errorResponse));
    }

    private void sendGatewayTimeoutResponse(HttpServletRequest request, HttpServletResponse response,
                                            String detail, String path) throws IOException {
        addCorsHeaders(request, response);
        ErrorResponse errorResponse = ErrorResponse.gatewayTimeout(detail, path);

        response.setStatus(HttpServletResponse.SC_GATEWAY_TIMEOUT);
        response.setContentType(MediaType.APPLICATION_JSON_VALUE);
        response.getWriter().write(objectMapper.writeValueAsString(errorResponse));
    }

    private void sendBadGatewayResponse(HttpServletRequest request, HttpServletResponse response,
                                        String detail, String path) throws IOException {
        addCorsHeaders(request, response);
        ErrorResponse errorResponse = ErrorResponse.badGateway(detail, path);

        response.setStatus(HttpServletResponse.SC_BAD_GATEWAY);
        response.setContentType(MediaType.APPLICATION_JSON_VALUE);
        response.getWriter().write(objectMapper.writeValueAsString(errorResponse));
    }

    private void addCorsHeaders(HttpServletRequest request, HttpServletResponse response) {
        String origin = request.getHeader("Origin");
        if (origin != null) {
            response.setHeader("Access-Control-Allow-Origin", origin);
            response.setHeader("Access-Control-Allow-Credentials", "true");
        }
    }

    /**
     * Wrapper to add custom headers to the request for downstream services.
     */
    private static class HeaderMapRequestWrapper extends HttpServletRequestWrapper {
        private final Map<String, String> additionalHeaders;

        public HeaderMapRequestWrapper(HttpServletRequest request, Map<String, String> additionalHeaders) {
            super(request);
            this.additionalHeaders = additionalHeaders;
        }

        @Override
        public String getHeader(String name) {
            String header = additionalHeaders.get(name);
            return header != null ? header : super.getHeader(name);
        }

        @Override
        public Enumeration<String> getHeaderNames() {
            Set<String> headerNames = new HashSet<>(additionalHeaders.keySet());
            Enumeration<String> originalNames = super.getHeaderNames();
            while (originalNames.hasMoreElements()) {
                headerNames.add(originalNames.nextElement());
            }
            return Collections.enumeration(headerNames);
        }

        @Override
        public Enumeration<String> getHeaders(String name) {
            String additionalHeader = additionalHeaders.get(name);
            if (additionalHeader != null) {
                return Collections.enumeration(Collections.singletonList(additionalHeader));
            }
            return super.getHeaders(name);
        }
    }
}
