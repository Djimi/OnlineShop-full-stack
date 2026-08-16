package com.onlineshop.gateway.validation;

import com.onlineshop.gateway.exception.InvalidTokenFormatException;
import io.micrometer.core.annotation.Timed;
import lombok.extern.slf4j.Slf4j;
import org.springframework.stereotype.Component;

@Component
@Slf4j
public class TokenSanitizer {

    private static final int MAX_TOKEN_LENGTH = 8192; // 8KB

    /**
     * Validates and sanitizes a token.
     * Throws InvalidTokenFormatException if the token format is invalid.
     *
     * @param token the token to validate
     * @throws InvalidTokenFormatException if token is null, empty, too long, or contains invalid characters
     */
    @Timed(value = "token.validation.time", description = "Time taken to validate tokens")
    public void validate(String token) {
        if (token == null || token.isBlank()) {
            log.debug("Token validation failed: token is null or empty");
            throw new InvalidTokenFormatException("Token is null or empty");
        }

        if (token.length() > MAX_TOKEN_LENGTH) {
            log.warn("Token validation failed: token exceeds maximum length of {} bytes", MAX_TOKEN_LENGTH);
            throw new InvalidTokenFormatException("Token exceeds maximum length of " + MAX_TOKEN_LENGTH + " bytes");
        }

        if (containsNullByte(token)) {
            log.warn("Token validation failed: token contains null bytes");
            throw new InvalidTokenFormatException("Token contains null bytes");
        }
    }

    /**
     * Sanitizes username before adding to headers.
     *
     * @param username the username to sanitize
     * @return sanitized username
     */
    public String sanitizeUsername(String username) {
        if (username == null) {
            return "";
        }

        // Remove control characters to prevent header injection
        return username.replaceAll("\\p{Cc}", "");
    }

    private boolean containsNullByte(String token) {
        return token.indexOf('\0') != -1;
    }
}
