package com.onlineshop.gateway.filter;

import java.util.concurrent.CompletionException;

import jakarta.servlet.FilterChain;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.springframework.mock.web.MockHttpServletRequest;
import org.springframework.mock.web.MockHttpServletResponse;

import com.onlineshop.gateway.dto.ValidateResponse;
import com.onlineshop.gateway.exception.GatewayTimeoutException;
import com.onlineshop.gateway.metrics.GatewayMetrics;
import com.onlineshop.gateway.ratelimit.RateLimitService;
import com.onlineshop.gateway.service.AuthValidationService;
import com.onlineshop.gateway.validation.TokenSanitizer;
import org.springframework.beans.factory.ObjectProvider;
import tools.jackson.databind.json.JsonMapper;

import static org.assertj.core.api.Assertions.assertThat;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.verifyNoInteractions;
import static org.mockito.Mockito.when;

class AuthenticationFilterTest {

    private AuthValidationService authValidationService;
    private RateLimitService rateLimitService;
    private AuthenticationFilter authenticationFilter;

    @BeforeEach
    void setUp() {
        authValidationService = mock(AuthValidationService.class);
        rateLimitService = mock(RateLimitService.class);
        authenticationFilter = new AuthenticationFilter(
                authValidationService,
                JsonMapper.builder().build(),
                mock(TokenSanitizer.class),
                providerReturning(rateLimitService),
                mock(GatewayMetrics.class));
    }

    @SuppressWarnings("unchecked")
    private ObjectProvider<RateLimitService> providerReturning(RateLimitService service) {
        ObjectProvider<RateLimitService> provider = mock(ObjectProvider.class);
        when(provider.getIfAvailable()).thenReturn(service);
        return provider;
    }

    @Test
    void wrappedGatewayTimeoutIsReturnedAs504() throws Exception {
        MockHttpServletRequest request = new MockHttpServletRequest("GET", "/items");
        request.addHeader("Authorization", "Bearer valid-looking-token");
        MockHttpServletResponse response = new MockHttpServletResponse();
        FilterChain filterChain = mock(FilterChain.class);

        when(authValidationService.validateToken("valid-looking-token"))
                .thenThrow(new CompletionException(
                        new GatewayTimeoutException("Auth service request timed out")));

        authenticationFilter.doFilter(request, response, filterChain);

        assertThat(response.getStatus()).isEqualTo(504);
        verifyNoInteractions(filterChain);
    }

    @Test
    void invalidTokenIsReturnedAs401WhenRateLimitAllows() throws Exception {
        MockHttpServletRequest request = new MockHttpServletRequest("GET", "/items");
        request.addHeader("Authorization", "Bearer invalid-token");
        MockHttpServletResponse response = new MockHttpServletResponse();
        FilterChain filterChain = mock(FilterChain.class);

        when(rateLimitService.tryConsumeFailedAuth(any())).thenReturn(true);
        when(authValidationService.validateToken("invalid-token"))
                .thenReturn(ValidateResponse.builder().valid(false).build());

        authenticationFilter.doFilter(request, response, filterChain);

        assertThat(response.getStatus()).isEqualTo(401);
        verifyNoInteractions(filterChain);
    }

    @Test
    void invalidTokenIsReturnedAs429WhenRateLimitExhausted() throws Exception {
        MockHttpServletRequest request = new MockHttpServletRequest("GET", "/items");
        request.addHeader("Authorization", "Bearer invalid-token");
        MockHttpServletResponse response = new MockHttpServletResponse();
        FilterChain filterChain = mock(FilterChain.class);

        when(rateLimitService.tryConsumeFailedAuth(any())).thenReturn(false);
        when(authValidationService.validateToken("invalid-token"))
                .thenReturn(ValidateResponse.builder().valid(false).build());

        authenticationFilter.doFilter(request, response, filterChain);

        assertThat(response.getStatus()).isEqualTo(429);
        verifyNoInteractions(filterChain);
    }

    @Test
    void missingAuthorizationHeaderIsThrottled() throws Exception {
        MockHttpServletRequest request = new MockHttpServletRequest("GET", "/items");
        MockHttpServletResponse response = new MockHttpServletResponse();
        FilterChain filterChain = mock(FilterChain.class);

        when(rateLimitService.tryConsumeFailedAuth(any())).thenReturn(false);

        authenticationFilter.doFilter(request, response, filterChain);

        assertThat(response.getStatus()).isEqualTo(429);
        verifyNoInteractions(filterChain);
    }
}