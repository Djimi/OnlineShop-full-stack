package com.onlineshop.auth.service;

import com.onlineshop.auth.dto.LoginRequest;
import com.onlineshop.auth.dto.LoginResponse;
import com.onlineshop.auth.dto.RegisterRequest;
import com.onlineshop.auth.dto.RegisterResponse;
import com.onlineshop.auth.dto.ValidateResponse;
import com.onlineshop.auth.entity.Session;
import com.onlineshop.auth.entity.User;
import com.onlineshop.auth.exception.InvalidUsernameOrPasswordException;
import com.onlineshop.auth.exception.UserAlreadyExistsException;
import com.onlineshop.auth.repository.SessionRepository;
import com.onlineshop.auth.repository.UserRepository;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.extension.ExtendWith;
import org.mockito.ArgumentCaptor;
import org.mockito.Mock;
import org.mockito.junit.jupiter.MockitoExtension;
import org.springframework.security.crypto.password.PasswordEncoder;

import java.security.MessageDigest;
import java.security.NoSuchAlgorithmException;
import java.security.SecureRandom;
import java.time.Clock;
import java.time.Instant;
import java.time.ZoneId;
import java.util.Optional;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.Mockito.doAnswer;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;

@ExtendWith(MockitoExtension.class)
class AuthServiceTest {

    private static final long SESSION_EXPIRATION_SECONDS = 3600L;
    private static final Instant FIXED_TIME = Instant.parse("2025-01-15T10:00:00Z");

    @Mock
    private UserRepository userRepository;

    @Mock
    private SessionRepository sessionRepository;

    @Mock
    private SessionRepository.SessionValidationProjection validationProjection;

    @Mock
    private PasswordEncoder passwordEncoder;

    @Mock
    private SecureRandom secureRandom;

    private Clock fixedClock;
    private AuthService authService;

    @BeforeEach
    void setUp() {
        fixedClock = Clock.fixed(FIXED_TIME, ZoneId.of("UTC"));
        authService = new AuthService(
                userRepository,
                sessionRepository,
                passwordEncoder,
                secureRandom,
                fixedClock,
                SESSION_EXPIRATION_SECONDS
        );
    }

    // ==================== register() tests ====================

    @Test
    void register_whenUsernameAvailable_createsUserAndReturnsResponse() {
        RegisterRequest request = new RegisterRequest("testuser", "password123");

        when(userRepository.existsByNormalizedUsername("testuser")).thenReturn(false);
        when(passwordEncoder.encode("password123")).thenReturn("encodedPassword");
        when(userRepository.save(any(User.class))).thenAnswer(invocation -> {
            User user = invocation.getArgument(0);
            user.setId(1L);
            user.setCreatedAt(FIXED_TIME);
            user.setUpdatedAt(FIXED_TIME);
            return user;
        });

        RegisterResponse response = authService.register(request);

        assertThat(response.getUserId()).isEqualTo(1L);
        assertThat(response.getUsername()).isEqualTo("testuser");
        assertThat(response.getCreatedAt()).isEqualTo(FIXED_TIME);
        assertThat(response.getUpdatedAt()).isEqualTo(FIXED_TIME);

        ArgumentCaptor<User> userCaptor = ArgumentCaptor.forClass(User.class);
        verify(userRepository).save(userCaptor.capture());
        User savedUser = userCaptor.getValue();
        assertThat(savedUser.getUsername()).isEqualTo("testuser");
        assertThat(savedUser.getPasswordHash()).isEqualTo("encodedPassword");
    }

    @Test
    void register_whenUsernameExists_throwsUserAlreadyExistsException() {
        RegisterRequest request = new RegisterRequest("existinguser", "password123");
        when(userRepository.existsByNormalizedUsername("existinguser")).thenReturn(true);

        assertThatThrownBy(() -> authService.register(request))
                .isInstanceOf(UserAlreadyExistsException.class);
    }

    // ==================== login() tests ====================

    @Test
    void login_whenCredentialsValid_createsSessionAndReturnsToken() {
        LoginRequest request = new LoginRequest("testuser", "password123");
        User user = createUser(1L, "testuser", "encodedPassword");
        Instant expectedExpiresAt = FIXED_TIME.plusSeconds(SESSION_EXPIRATION_SECONDS);

        when(userRepository.findByNormalizedUsername("testuser")).thenReturn(Optional.of(user));
        when(passwordEncoder.matches("password123", "encodedPassword")).thenReturn(true);
        mockSecureRandomBytes();
        when(sessionRepository.save(any(Session.class))).thenAnswer(invocation -> {
            Session session = invocation.getArgument(0);
            session.setId(1L);
            session.setCreatedAt(FIXED_TIME);
            return session;
        });

        LoginResponse response = authService.login(request);

        assertThat(response.getToken()).hasSize(64);
        assertThat(response.getUserId()).isEqualTo(1L);
        assertThat(response.getUsername()).isEqualTo("testuser");
        assertThat(response.getExpiresAt()).isEqualTo(expectedExpiresAt);
        assertThat(response.getTokenType()).isEqualTo("Bearer");
        assertThat(response.getExpiresIn()).isEqualTo(SESSION_EXPIRATION_SECONDS);

        ArgumentCaptor<Session> sessionCaptor = ArgumentCaptor.forClass(Session.class);
        verify(sessionRepository).save(sessionCaptor.capture());
        Session savedSession = sessionCaptor.getValue();
        assertThat(savedSession.getUser()).isEqualTo(user);
        assertThat(savedSession.getExpiresAt()).isEqualTo(expectedExpiresAt);
        assertThat(savedSession.getTokenHash()).hasSize(64);
    }

    @Test
    void login_whenUserNotFound_throwsInvalidUsernameOrPasswordException() {
        LoginRequest request = new LoginRequest("nonexistent", "password123");
        when(userRepository.findByNormalizedUsername("nonexistent")).thenReturn(Optional.empty());

        assertThatThrownBy(() -> authService.login(request))
                .isInstanceOf(InvalidUsernameOrPasswordException.class);
    }

    @Test
    void login_whenPasswordInvalid_throwsInvalidUsernameOrPasswordException() {
        LoginRequest request = new LoginRequest("testuser", "wrongpassword");
        User user = createUser(1L, "testuser", "encodedPassword");

        when(userRepository.findByNormalizedUsername("testuser")).thenReturn(Optional.of(user));
        when(passwordEncoder.matches("wrongpassword", "encodedPassword")).thenReturn(false);

        assertThatThrownBy(() -> authService.login(request))
                .isInstanceOf(InvalidUsernameOrPasswordException.class);
    }

    // ==================== validateToken() tests ====================

    @Test
    void validateToken_whenTokenValid_returnsValidResponse() {
        String token = "validtoken";
        String tokenHash = hashToken(token);

        when(validationProjection.getCreatedAt()).thenReturn(FIXED_TIME);
        when(validationProjection.getExpiresAt()).thenReturn(FIXED_TIME.plusSeconds(3600));
        when(validationProjection.getUserId()).thenReturn(1L);
        when(validationProjection.getUsername()).thenReturn("testuser");
        when(sessionRepository.findValidationProjectionByTokenHash(tokenHash))
                .thenReturn(Optional.of(validationProjection));

        ValidateResponse response = authService.validateToken(token);

        assertThat(response.isValid()).isTrue();
        assertThat(response.getUserId()).isEqualTo(1L);
        assertThat(response.getUsername()).isEqualTo("testuser");
        assertThat(response.getExpiresAt()).isEqualTo(FIXED_TIME.plusSeconds(3600));
    }

    @Test
    void validateToken_whenTokenNotFound_returnsInvalidResponse() {
        String token = "invalidtoken";
        String tokenHash = hashToken(token);

        when(sessionRepository.findValidationProjectionByTokenHash(tokenHash)).thenReturn(Optional.empty());

        ValidateResponse response = authService.validateToken(token);

        assertThat(response.isValid()).isFalse();
        assertThat(response.getUserId()).isNull();
        assertThat(response.getUsername()).isNull();
        assertThat(response.getCreatedAt()).isNull();
        assertThat(response.getExpiresAt()).isNull();
    }

    @Test
    void validateToken_whenSessionExpired_returnsInvalidResponse() {
        String token = "expiredtoken";
        String tokenHash = hashToken(token);
        Instant sessionCreatedAt = FIXED_TIME.minusSeconds(7200);
        Instant sessionExpiresAt = FIXED_TIME.minusSeconds(3600);

        when(validationProjection.getCreatedAt()).thenReturn(sessionCreatedAt);
        when(validationProjection.getExpiresAt()).thenReturn(sessionExpiresAt);
        when(sessionRepository.findValidationProjectionByTokenHash(tokenHash))
                .thenReturn(Optional.of(validationProjection));

        ValidateResponse response = authService.validateToken(token);

        assertThat(response.isValid()).isFalse();
        assertThat(response.getUserId()).isNull();
        assertThat(response.getUsername()).isNull();
        assertThat(response.getCreatedAt()).isNull();
        assertThat(response.getExpiresAt()).isNull();
    }

    @Test
    void validateToken_whenCurrentTimeBeforeCreatedAt_returnsInvalidResponse() {
        String token = "futuretoken";
        String tokenHash = hashToken(token);
        Instant sessionCreatedAt = FIXED_TIME.plusSeconds(3600);

        when(validationProjection.getCreatedAt()).thenReturn(sessionCreatedAt);
        when(sessionRepository.findValidationProjectionByTokenHash(tokenHash))
                .thenReturn(Optional.of(validationProjection));

        ValidateResponse response = authService.validateToken(token);

        assertThat(response.isValid()).isFalse();
        assertThat(response.getUserId()).isNull();
        assertThat(response.getUsername()).isNull();
        assertThat(response.getCreatedAt()).isNull();
        assertThat(response.getExpiresAt()).isNull();
    }

    // ==================== Helper methods ====================

    private User createUser(Long id, String username, String passwordHash) {
        User user = new User();
        user.setId(id);
        user.setUsername(username);
        user.setPasswordHash(passwordHash);
        user.setCreatedAt(FIXED_TIME);
        user.setUpdatedAt(FIXED_TIME);
        return user;
    }

    private void mockSecureRandomBytes() {
        doAnswer(invocation -> {
            byte[] bytes = invocation.getArgument(0);
            for (int i = 0; i < bytes.length; i++) {
                bytes[i] = (byte) i;
            }
            return null;
        }).when(secureRandom).nextBytes(any(byte[].class));
    }

    private String hashToken(String token) {
        try {
            MessageDigest digest = MessageDigest.getInstance("SHA-256");
            byte[] hash = digest.digest(token.getBytes());
            StringBuilder sb = new StringBuilder();
            for (byte b : hash) {
                sb.append(String.format("%02x", b));
            }
            return sb.toString();
        } catch (NoSuchAlgorithmException e) {
            throw new RuntimeException("SHA-256 algorithm not available", e);
        }
    }
}
