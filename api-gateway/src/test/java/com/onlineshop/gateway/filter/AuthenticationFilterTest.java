package com.onlineshop.gateway.filter;

import java.util.concurrent.CompletionException;

import jakarta.servlet.FilterChain;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.springframework.mock.web.MockHttpServletRequest;
import org.springframework.mock.web.MockHttpServletResponse;

import com.onlineshop.gateway.exception.GatewayTimeoutException;
import com.onlineshop.gateway.service.AuthValidationService;
import com.onlineshop.gateway.validation.TokenSanitizer;
import tools.jackson.databind.json.JsonMapper;

import static org.assertj.core.api.Assertions.assertThat;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.verifyNoInteractions;
import static org.mockito.Mockito.when;

class AuthenticationFilterTest {

    private AuthValidationService authValidationService;
    private AuthenticationFilter authenticationFilter;

    @BeforeEach
    void setUp() {
        authValidationService = mock(AuthValidationService.class);
        authenticationFilter = new AuthenticationFilter(
                authValidationService,
                JsonMapper.builder().build(),
                mock(TokenSanitizer.class));
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
}
